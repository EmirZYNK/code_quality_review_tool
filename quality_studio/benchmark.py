"""Dinamik analiz: önce/sonra kodunu çalıştırıp süre, bellek ve çıktı eşitliğini ölçer.

İki çalışma modu vardır:
  * "docker": izole kutu (ağ yok, salt-okunur dosya sistemi, yetkiler kapalı). Önerilen moddur.
  * "yerel": Docker yokken yedek yöntem. Ayrı bir Python süreci, boş ortam, geçici klasör, süre sınırı
    (Linux/macOS'ta ayrıca bellek ve CPU sınırı). Gerçek bir izolasyon DEĞİLDİR; yalnızca güvenilir kod içindir.

Tasarım notları:
  * Süre ve bellek ayrı turlarda ölçülür; tracemalloc kodu ~10 kat yavaşlatır, süreyi şişirirdi.
  * Argümanlar zamanlayıcı başlamadan kopyalanır, kopyalama maliyeti ölçüme girmez.
  * İmaj indirme kendi (daha uzun) zaman aşımına sahiptir, çalışma süresine sayılmaz.
  * Her konteynerin benzersiz adı vardır; zaman aşımına uğrayan konteyner açıkça durdurulur.
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE = "python:3.12-alpine"

RUNNER = r'''
import copy, json, pathlib, statistics, sys, time, tracemalloc

WORK = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '/work')
cfg = json.loads((WORK / 'config.json').read_text())
ns_before = {'__name__': 'benchmark_before'}
ns_after = {'__name__': 'benchmark_after'}


def load(path, namespace):
    exec(compile(pathlib.Path(path).read_text(), path, 'exec'), namespace)
    fn = namespace[cfg['function']]
    if not callable(fn):
        raise ValueError('Hedef çağrılabilir değil')
    return fn


def fresh_args():
    return copy.deepcopy(cfg['args']), copy.deepcopy(cfg['kwargs'])


def outcome(fn):
    args, kwargs = fresh_args()
    try:
        return ('ok', fn(*args, **kwargs))
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc))


def measure_time(fn):
    for _ in range(3):
        args, kwargs = fresh_args()
        fn(*args, **kwargs)
    samples = []
    for _ in range(cfg['repeats']):
        args, kwargs = fresh_args()
        start = time.perf_counter_ns()
        fn(*args, **kwargs)
        samples.append((time.perf_counter_ns() - start) / 1e6)
    return {'median_ms': statistics.median(samples), 'min_ms': min(samples)}


def measure_memory(fn):
    peaks = []
    for _ in range(cfg['memory_repeats']):
        args, kwargs = fresh_args()
        tracemalloc.start()
        try:
            fn(*args, **kwargs)
            peaks.append(tracemalloc.get_traced_memory()[1])
        finally:
            tracemalloc.stop()
    return {'median_peak_bytes': statistics.median(peaks)}


try:
    f = load(str(WORK / 'before.py'), ns_before)
    g = load(str(WORK / 'after.py'), ns_after)
    a, b = outcome(f), outcome(g)
    if a != b:
        print(json.dumps({'ok': False, 'error': 'Önce ve Sonra sürümlerinin çıktısı ya da hatası farklı',
                          'before_outcome': repr(a)[:400], 'after_outcome': repr(b)[:400]}))
        raise SystemExit(0)
    result = {'ok': True, 'repeats': cfg['repeats'], 'memory_repeats': cfg['memory_repeats'],
              'equivalent_for_test_input': True}
    for label, fn in (('before', f), ('after', g)):
        result[label] = {**measure_time(fn), **measure_memory(fn)}
    print(json.dumps(result))
except SystemExit:
    raise
except Exception as exc:
    print(json.dumps({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:250]}))
'''


def _docker(*args: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def docker_status() -> dict:
    """Docker'ın durumunu üç basamakta döndürür: komut var mı, servis çalışıyor mu, imaj indirilmiş mi."""
    status = {"cli": bool(shutil.which("docker")), "daemon": False, "image": False}
    if not status["cli"]:
        return status
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        status["daemon"] = _docker("info", timeout=8).returncode == 0
        if status["daemon"]:
            status["image"] = _docker("image", "inspect", IMAGE, timeout=8).returncode == 0
    return status


def ensure_image(pull_timeout: int = 300) -> str | None:
    """İmaj hazırsa None, değilse hata mesajı döndürür."""
    try:
        if _docker("image", "inspect", IMAGE, timeout=20).returncode == 0:
            return None
        pulled = _docker("pull", IMAGE, timeout=pull_timeout)
    except subprocess.TimeoutExpired:
        return f"{IMAGE} imajı {pull_timeout} sn içinde indirilemedi; elle deneyin: docker pull {IMAGE}"
    except OSError as exc:
        return str(exc)
    return None if pulled.returncode == 0 else ("İmaj indirilemedi: " + pulled.stderr)[-300:]


def _kill(name: str) -> None:
    """Zaman aşımına uğrayan konteyneri en iyi çabayla durdurur."""
    for command in (("kill", name), ("rm", "-f", name)):
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            _docker(*command, timeout=15)


@dataclass(frozen=True)
class BenchmarkOptions:
    repeats: int = 15
    timeout: int = 30
    pull_timeout: int = 300
    mode: str = "docker"  # "docker" (izole) veya "yerel" (Docker'sız yedek, izole değil)


def _validate(before: str, after: str, function: str, args, kwargs) -> str | None:
    for source in (before, after):
        ast.parse(source)
    if not function.isidentifier() or function.startswith("_"):
        return "Genel (alt çizgiyle başlamayan) bir üst düzey fonksiyon adı seçin."
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return "Konumsal argümanlar JSON dizisi, isimli argümanlar JSON nesnesi olmalıdır."
    return None


def _docker_command(folder: Path, name: str) -> list[str]:
    return ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "256m", "--cpus", "1", "--user", "65534:65534",
            "--mount", f"type=bind,src={folder},dst=/work,readonly",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            IMAGE, "python", "-I", "/work/runner.py"]


def _limit_resources() -> None:  # pragma: no cover - yalnızca alt süreçte, POSIX'te çalışır
    import resource
    for kind, value in ((resource.RLIMIT_AS, 512 * 1024 * 1024), (resource.RLIMIT_CPU, 60),
                        (resource.RLIMIT_FSIZE, 1024 * 1024), (resource.RLIMIT_NPROC, 64)):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(kind, (value, value))


def _local_command(folder: Path) -> list[str]:
    return [sys.executable, "-I", str(folder / "runner.py"), str(folder)]


def _parse_output(returncode: int, stdout: str, stderr: str, failure_label: str) -> dict:
    if returncode != 0:
        return {"ok": False, "error": (failure_label + ": " + stderr)[-500:]}
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "Ölçüm geçerli bir JSON döndürmedi. " + stderr[-200:]}


def _run_container(command: list[str], name: str, timeout: int) -> dict:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        _kill(name)
        process.communicate()
        return {"ok": False, "error": f"Ölçüm {timeout} sn'yi aştı ve konteyner durduruldu."}
    return _parse_output(process.returncode, stdout, stderr, "Docker çalışmadı")


def _run_local(command: list[str], folder: Path, timeout: int) -> dict:
    """Docker'sız yedek: boş ortam, geçici çalışma klasörü, süre sınırı (POSIX'te ek kaynak sınırı)."""
    extra = {"preexec_fn": _limit_resources} if os.name == "posix" else {}
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   cwd=folder, env={"PYTHONIOENCODING": "utf-8"}, **extra)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return {"ok": False, "error": f"Ölçüm {timeout} sn'yi aştı ve süreç durduruldu."}
    return _parse_output(process.returncode, stdout, stderr, "Yerel süreç çalışmadı")


def summarize(result: dict, noise: float = 0.10) -> dict:
    """Ölçüm sonucunu yorumlar: hız oranı, bellek oranı ve Türkçe karar cümlesi.

    `noise`: bu orandan küçük farklar ölçüm gürültüsü sayılır ve "belirgin fark yok" denir.
    """
    if not result.get("ok"):
        return {"ok": False}
    before, after = result["before"], result["after"]
    speed = before["median_ms"] / after["median_ms"] if after["median_ms"] else None
    memory = before["median_peak_bytes"] / after["median_peak_bytes"] if after["median_peak_bytes"] else None

    def verdict(ratio: float | None, better: str, worse: str) -> str:
        if ratio is None:
            return "ölçülemedi"
        if ratio > 1 + noise:
            return better
        if ratio < 1 / (1 + noise):
            return worse
        return "belirgin fark yok"

    return {"ok": True, "speedup": speed, "memory_ratio": memory,
            "time_verdict": verdict(speed, "Sonra sürümü daha hızlı", "Sonra sürümü daha yavaş"),
            "memory_verdict": verdict(memory, "Sonra sürümü daha az bellek ayırıyor",
                                      "Sonra sürümü daha çok bellek ayırıyor")}


def benchmark(before: str, after: str, function: str, args=None, kwargs=None,
              options: BenchmarkOptions = BenchmarkOptions()) -> dict:
    """Bir fonksiyonun iki sürümünü çalıştırıp karşılaştırır (Docker kutusunda ya da yerel yedek modda)."""
    if options.mode not in ("docker", "yerel"):
        return {"ok": False, "error": "Geçersiz mod: 'docker' veya 'yerel' olmalı."}
    if options.mode == "docker" and not shutil.which("docker"):
        return {"ok": False, "error": "Docker komutu kurulu değil ya da PATH içinde yok. "
                                      "Docker Desktop'ı kurun veya 'yerel' yedek modu kullanın."}
    args = [] if args is None else args
    kwargs = {} if kwargs is None else kwargs
    try:
        problem = _validate(before, after, function, args, kwargs)
    except SyntaxError as exc:
        return {"ok": False, "error": f"Sözdizimi hatası (satır {exc.lineno}): {exc.msg}"}
    if problem:
        return {"ok": False, "error": problem}
    repeats = max(3, min(int(options.repeats), 40))
    config = {"function": function, "args": args, "kwargs": kwargs,
              "repeats": repeats, "memory_repeats": min(5, repeats)}
    if options.mode == "docker":
        problem = ensure_image(options.pull_timeout)
        if problem:
            return {"ok": False, "error": problem}
    name = f"cqs-bench-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="quality_bench_") as tmp:
        folder = Path(tmp)
        folder.chmod(0o755)
        files = {"before.py": before, "after.py": after, "config.json": json.dumps(config), "runner.py": RUNNER}
        for filename, content in files.items():
            (folder / filename).write_text(content, encoding="utf-8")
        if options.mode == "docker":
            result = _run_container(_docker_command(folder, name), name, options.timeout)
        else:
            result = _run_local(_local_command(folder), folder, options.timeout)
    if result.get("ok"):
        result["mode"] = options.mode
    return result
