"""Dinamik analiz: önce/sonra kodunu çalıştırıp süre, bellek ve çıktı eşitliğini ölçer (bir ya da birkaç test girdisiyle).

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
import collections.abc, contextlib, copy, io, itertools, json, pathlib, statistics, sys, time, tracemalloc

WORK = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '/work')
cfg = json.loads((WORK / 'config.json').read_text())
CASES = cfg.get('cases') or [{'label': 'girdi 1', 'args': cfg['args'], 'kwargs': cfg['kwargs']}]
DEFAULT_FUNCTION = cfg['function']
ITER_LIMIT = 10000   # generator/yineleyici sonuçları en çok bu kadar eleman okunarak karşılaştırılır
ns_before = {'__name__': 'benchmark_before'}
ns_after = {'__name__': 'benchmark_after'}


class Null:
    """Ölçüm turlarında fonksiyonun yazdıkları çöpe gider (zamanı ve belleği bozmasın)."""

    def write(self, text):
        return len(text)

    def flush(self):
        pass


def silent():
    return contextlib.redirect_stdout(Null()), contextlib.redirect_stderr(Null())


def load(path, namespace):
    """Modülü çalıştırır; yüklenirken yazdırdığı çıktıyı ve hatayı döndürür (davranışın parçasıdır)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        exec(compile(pathlib.Path(path).read_text(), path, 'exec'), namespace)
    return out.getvalue(), err.getvalue()


def get_function(namespace, name):
    fn = namespace.get(name)
    if not callable(fn):
        raise ValueError("'" + name + "' çağrılabilir bir fonksiyon olarak bulunamadı")
    return fn


def fresh_args(case):
    return copy.deepcopy(case['args']), copy.deepcopy(case['kwargs'])


def materialize(value):
    """Generator/yineleyici döndüren fonksiyonlar nesne kimliğiyle değil, ürettikleri değerlerle karşılaştırılır."""
    if isinstance(value, collections.abc.Iterator):
        items = list(itertools.islice(value, ITER_LIMIT + 1))
        return ('yineleyici', items[:ITER_LIMIT], len(items) > ITER_LIMIT)
    return value


def consume(value):
    if isinstance(value, collections.abc.Iterator):
        for _ in itertools.islice(value, ITER_LIMIT):
            pass


def outcome(fn, case):
    """Tek çağrının TÜM gözlenebilir davranışı: dönüş/hata, stdout, stderr ve girdilerin çağrı sonrası hali."""
    args, kwargs = fresh_args(case)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = ('ok', materialize(fn(*args, **kwargs)))
    except Exception as exc:
        status = ('error', type(exc).__name__, str(exc))
    return {'status': status, 'out': out.getvalue(), 'err': err.getvalue(), 'args': args, 'kwargs': kwargs}


def measure_time(fn, case):
    quiet = silent()
    with quiet[0], quiet[1]:
        for _ in range(3):
            args, kwargs = fresh_args(case)
            consume(fn(*args, **kwargs))
        samples = []
        for _ in range(cfg['repeats']):
            args, kwargs = fresh_args(case)
            start = time.perf_counter_ns()
            consume(fn(*args, **kwargs))
            samples.append((time.perf_counter_ns() - start) / 1e6)
    return {'median_ms': statistics.median(samples), 'min_ms': min(samples)}


def measure_memory(fn, case):
    peaks = []
    quiet = silent()
    with quiet[0], quiet[1]:
        for _ in range(cfg['memory_repeats']):
            args, kwargs = fresh_args(case)
            tracemalloc.start()
            try:
                consume(fn(*args, **kwargs))
                peaks.append(tracemalloc.get_traced_memory()[1])
            finally:
                tracemalloc.stop()
    return {'median_peak_bytes': statistics.median(peaks)}


def same_value(a, b):
    try:
        if a == b:
            return True
    except Exception:
        pass
    return repr(a) == repr(b)   # NaN gibi kendine eşit olmayan ama aynı görünen değerler için


def differences(a, b):
    reasons = []
    if not same_value(a['status'], b['status']):
        reasons.append('dönüş değeri ya da hata')
    if a['out'] != b['out']:
        reasons.append('ekrana yazdırılan çıktı (print)')
    if a['err'] != b['err']:
        reasons.append('stderr çıktısı')
    if not same_value((a['args'], a['kwargs']), (b['args'], b['kwargs'])):
        reasons.append('girdi nesnelerinin yerinde değiştirilmesi')
    return reasons


def describe(result, case):
    text = repr(result['status'])[:260]
    if result['out']:
        text += ' | print: ' + repr(result['out'])[:80]
    if not same_value((result['args'], result['kwargs']), (case['args'], case['kwargs'])):
        text += ' | girdi sonrası: ' + repr(result['args'])[:80]
    return text[:400]


def compare_outcomes(functions_before, functions_after):
    rows = []
    for case in CASES:
        name = case.get('function') or DEFAULT_FUNCTION
        a, b = outcome(functions_before[name], case), outcome(functions_after[name], case)
        reasons = differences(a, b)
        rows.append({'label': case['label'], 'function': name, 'same': not reasons, 'kind': a['status'][0],
                     'diff': reasons, 'before_outcome': describe(a, case), 'after_outcome': describe(b, case)})
    return rows


def fail(message, rows, **extra):
    print(json.dumps({'ok': False, 'error': message, 'cases': rows, **extra}))
    raise SystemExit(0)


def per_function(timed):
    result = {}
    for name in sorted({r['function'] for r in timed}):
        mine = [r for r in timed if r['function'] == name]
        top = max(mine, key=lambda r: r['before']['median_ms'])
        result[name] = {'case': top['label'], 'before': top['before'], 'after': top['after'], 'cases': len(mine)}
    return result


try:
    load_before = load(str(WORK / 'before.py'), ns_before)
    load_after = load(str(WORK / 'after.py'), ns_after)
    names = sorted({c.get('function') or DEFAULT_FUNCTION for c in CASES})
    functions_before = {n: get_function(ns_before, n) for n in names}
    functions_after = {n: get_function(ns_after, n) for n in names}
    rows = compare_outcomes(functions_before, functions_after)
    if load_before != load_after:
        rows.insert(0, {'label': '(modül yüklenirken)', 'function': '', 'same': False, 'kind': 'load', 'timed': False,
                        'diff': ['modül yüklenirken yazdırılan çıktı'],
                        'before_outcome': repr(load_before)[:400], 'after_outcome': repr(load_after)[:400]})
    different = [r for r in rows if not r['same']]
    if different:
        fail('Önce ve Sonra sürümlerinin çıktısı ya da hatası farklı (' + ', '.join(different[0]['diff']) + ')', rows,
             before_outcome=different[0]['before_outcome'], after_outcome=different[0]['after_outcome'])
    by_label = {(c['label'], c.get('function') or DEFAULT_FUNCTION): c for c in CASES}
    for row in rows:
        row['timed'] = row['kind'] == 'ok'
        if row['timed']:
            case = by_label[(row['label'], row['function'])]
            for label, fns in (('before', functions_before), ('after', functions_after)):
                fn = fns[row['function']]
                row[label] = {**measure_time(fn, case), **measure_memory(fn, case)}
    timed = [r for r in rows if r['timed']]
    if not timed:
        fail('Tüm test girdilerinde iki sürüm de aynı hatayı verdi; ölçülecek bir çalışma yok', rows)
    headline = max(timed, key=lambda r: r['before']['median_ms'])
    print(json.dumps({'ok': True, 'repeats': cfg['repeats'], 'memory_repeats': cfg['memory_repeats'],
                      'equivalent_for_test_input': True, 'cases_total': len(rows), 'cases_same': len(rows),
                      'cases': rows, 'headline_case': headline['label'], 'functions_tested': names,
                      'by_function': per_function(timed), 'before': headline['before'], 'after': headline['after']}))
except SystemExit:
    raise
except Exception as exc:
    print(json.dumps({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:250]}))
'''


def find_docker() -> str | None:
    """docker komutunu bulur: önce PATH, sonra Docker Desktop'ın bilinen kurulum klasörleri.

    Windows'ta Docker kurulumundan önce açılmış pencerelerde PATH güncel olmayabilir; bu yedek arama
    kullanıcıyı bilgisayarı yeniden başlatmaktan kurtarır.
    """
    found = shutil.which("docker")
    if found:
        return found
    roots = ((os.environ.get("LOCALAPPDATA"), Path("Programs/DockerDesktop/resources/bin/docker.exe")),
             (os.environ.get("ProgramFiles"), Path("Docker/Docker/resources/bin/docker.exe")))
    for root, relative in roots:
        if root and (Path(root) / relative).is_file():
            return str(Path(root) / relative)
    return None


def _docker(*args: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run([find_docker() or "docker", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def docker_status() -> dict:
    """Docker'ın durumunu üç basamakta döndürür: komut var mı, servis çalışıyor mu, imaj indirilmiş mi."""
    status = {"cli": bool(find_docker()), "daemon": False, "image": False}
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


MAX_CASES = 16
MAX_CASE_CHARS = 20_000


def _validate_cases(cases) -> str | None:
    """Çoklu test girdisi listesini doğrular: en fazla MAX_CASES, her biri {label, args, kwargs}."""
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        return f"Test girdisi listesi 1 ile {MAX_CASES} arasında kayıt içermelidir."
    for case in cases:
        if not (isinstance(case, dict) and isinstance(case.get("args"), list) and isinstance(case.get("kwargs"), dict)):
            return "Her test girdisi {label, args (dizi), kwargs (nesne)} biçiminde olmalıdır."
        if len(json.dumps(case)) > MAX_CASE_CHARS:
            return "Bir test girdisi çok büyük (JSON olarak en fazla 20 000 karakter)."
        name = case.get("function")
        if name is not None and not (isinstance(name, str) and name.isidentifier() and not name.startswith("_")):
            return "Test girdisindeki fonksiyon adı genel (alt çizgiyle başlamayan) bir ad olmalı."
    return None


def _normalize_cases(cases: list[dict]) -> list[dict]:
    normalized = []
    for i, c in enumerate(cases, 1):
        record = {"label": str(c.get("label") or f"girdi {i}")[:40], "args": c["args"], "kwargs": c["kwargs"]}
        if c.get("function"):
            record["function"] = c["function"]
        normalized.append(record)
    return normalized


def _docker_command(folder: Path, name: str) -> list[str]:
    return [find_docker() or "docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
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


def _precheck(before: str, after: str, function: str, args, kwargs, cases, options: BenchmarkOptions) -> str | None:
    """Ölçümden önceki tüm denetimler; sorun varsa Türkçe hata mesajı, yoksa None."""
    if options.mode not in ("docker", "yerel"):
        return "Geçersiz mod: 'docker' veya 'yerel' olmalı."
    if options.mode == "docker" and not find_docker():
        return ("Docker komutu kurulu değil ya da PATH içinde yok. "
                "Docker Desktop'ı kurun veya 'yerel' yedek modu kullanın.")
    try:
        problem = _validate(before, after, function, args, kwargs)
    except SyntaxError as exc:
        return f"Sözdizimi hatası (satır {exc.lineno}): {exc.msg}"
    if problem or cases is None:
        return problem
    return _validate_cases(cases)


def _execute(config: dict, before: str, after: str, options: BenchmarkOptions) -> dict:
    """Dosyaları geçici klasöre yazar ve seçilen modda (Docker ya da yerel) çalıştırır."""
    name = f"cqs-bench-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="quality_bench_") as tmp:
        folder = Path(tmp)
        folder.chmod(0o755)
        files = {"before.py": before, "after.py": after, "config.json": json.dumps(config), "runner.py": RUNNER}
        for filename, content in files.items():
            (folder / filename).write_text(content, encoding="utf-8")
        if options.mode == "docker":
            return _run_container(_docker_command(folder, name), name, options.timeout)
        return _run_local(_local_command(folder), folder, options.timeout)


def benchmark(before: str, after: str, function: str, args=None, kwargs=None,
              options: BenchmarkOptions = BenchmarkOptions(), cases: list[dict] | None = None) -> dict:
    """Bir fonksiyonun iki sürümünü çalıştırıp karşılaştırır (Docker kutusunda ya da yerel yedek modda).

    `cases` verilirse her girdi için çıktı eşitliği denetlenir; süre ve bellek, Önce sürümünün en yavaş
    kaldığı girdi için raporlanır (diğer girdilerin ölçümleri `result["cases"]` içindedir).
    """
    args = [] if args is None else args
    kwargs = {} if kwargs is None else kwargs
    problem = _precheck(before, after, function, args, kwargs, cases, options)
    if problem:
        return {"ok": False, "error": problem}
    repeats = max(3, min(int(options.repeats), 40))
    config = {"function": function, "args": args, "kwargs": kwargs,
              "repeats": repeats, "memory_repeats": min(5, repeats)}
    if cases is not None:
        config["cases"] = _normalize_cases(cases)
    if options.mode == "docker":
        problem = ensure_image(options.pull_timeout)
        if problem:
            return {"ok": False, "error": problem}
    result = _execute(config, before, after, options)
    if result.get("ok"):
        result["mode"] = options.mode
    return result
