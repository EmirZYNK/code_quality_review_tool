"""Opt-in Docker-isolated benchmark. Never runs submitted code on the host.

Design notes:
  * Timing and memory are measured in separate rounds; tracemalloc slows code ~10x and would
    otherwise inflate every reported time.
  * Arguments are copied before the timer starts, so deep-copy cost is not measured.
  * Pulling the image has its own, longer timeout and is not charged to the run timeout.
  * Each container has a unique name so a timed-out run can be killed explicitly.
"""
from __future__ import annotations

import ast
import contextlib
import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE = "python:3.12-alpine"

RUNNER = r'''
import copy, json, pathlib, statistics, time, tracemalloc

cfg = json.loads(pathlib.Path('/work/config.json').read_text())
ns_before = {'__name__': 'benchmark_before'}
ns_after = {'__name__': 'benchmark_after'}


def load(path, namespace):
    exec(compile(pathlib.Path(path).read_text(), path, 'exec'), namespace)
    fn = namespace[cfg['function']]
    if not callable(fn):
        raise ValueError('Target is not callable')
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
    f = load('/work/before.py', ns_before)
    g = load('/work/after.py', ns_after)
    a, b = outcome(f), outcome(g)
    if a != b:
        print(json.dumps({'ok': False, 'error': 'Before/After outputs or exceptions differ',
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


def ensure_image(pull_timeout: int = 300) -> str | None:
    """Return None when the image is available, else an error message."""
    try:
        if _docker("image", "inspect", IMAGE, timeout=20).returncode == 0:
            return None
        pulled = _docker("pull", IMAGE, timeout=pull_timeout)
    except subprocess.TimeoutExpired:
        return f"Pulling {IMAGE} exceeded {pull_timeout}s; try 'docker pull {IMAGE}' manually."
    except OSError as exc:
        return str(exc)
    return None if pulled.returncode == 0 else ("Could not pull image: " + pulled.stderr)[-300:]


def _kill(name: str) -> None:
    """Best-effort stop of a container whose client process timed out."""
    for command in (("kill", name), ("rm", "-f", name)):
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            _docker(*command, timeout=15)


@dataclass(frozen=True)
class BenchmarkOptions:
    repeats: int = 15
    timeout: int = 30
    pull_timeout: int = 300


def _validate(before: str, after: str, function: str, args, kwargs) -> str | None:
    for source in (before, after):
        ast.parse(source)
    if not function.isidentifier() or function.startswith("_"):
        return "Choose a public top-level function name."
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return "Arguments must be a JSON list and keyword arguments a JSON object."
    return None


def _docker_command(folder: Path, name: str) -> list[str]:
    return ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "256m", "--cpus", "1", "--user", "65534:65534",
            "--mount", f"type=bind,src={folder},dst=/work,readonly",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            IMAGE, "python", "-I", "/work/runner.py"]


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
        return {"ok": False, "error": f"Benchmark exceeded {timeout}s and the container was stopped."}
    if process.returncode != 0:
        return {"ok": False, "error": ("Docker failed: " + stderr)[-500:]}
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "Benchmark did not return valid JSON. " + stderr[-200:]}


def benchmark(before: str, after: str, function: str, args=None, kwargs=None,
              options: BenchmarkOptions = BenchmarkOptions()) -> dict:
    """Compare two versions of a top-level function inside a locked-down container."""
    if not shutil.which("docker"):
        return {"ok": False, "error": "Docker CLI is not installed or not on PATH."}
    args = [] if args is None else args
    kwargs = {} if kwargs is None else kwargs
    problem = _validate(before, after, function, args, kwargs)
    if problem:
        return {"ok": False, "error": problem}
    repeats = max(3, min(int(options.repeats), 40))
    config = {"function": function, "args": args, "kwargs": kwargs,
              "repeats": repeats, "memory_repeats": min(5, repeats)}
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
        return _run_container(_docker_command(folder, name), name, options.timeout)
