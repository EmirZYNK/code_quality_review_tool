import subprocess
import unittest
from unittest.mock import MagicMock, patch

from quality_studio import benchmark as bench

SRC = "def f(x):\n    return x\n"


class ValidationTests(unittest.TestCase):
    def test_missing_docker(self):
        with patch.object(bench.shutil, "which", return_value=None):
            result = bench.benchmark(SRC, SRC, "f")
        self.assertFalse(result["ok"])
        self.assertIn("Docker", result["error"])
        self.assertIn("yerel", result["error"])

    def test_invalid_arguments(self):
        with patch.object(bench.shutil, "which", return_value="/usr/bin/docker"):
            self.assertFalse(bench.benchmark(SRC, SRC, "f", args={})["ok"])
            self.assertFalse(bench.benchmark(SRC, SRC, "_private")["ok"])

    def test_isolation_flags_present(self):
        command = bench._docker_command(bench.Path("/tmp/x"), "name")
        for flag in ("--network", "--read-only", "--cap-drop", "--memory", "--cpus", "--pids-limit",
                     "--user", "--security-opt", "--name"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--network") + 1], "none")


class ImageTests(unittest.TestCase):
    def test_image_present_skips_pull(self):
        with patch.object(bench, "_docker", return_value=MagicMock(returncode=0)) as docker:
            self.assertIsNone(bench.ensure_image())
        self.assertEqual(docker.call_count, 1)

    def test_pull_timeout_is_reported(self):
        def fake(*args, timeout):
            if args[0] == "image":
                return MagicMock(returncode=1)
            raise subprocess.TimeoutExpired("docker", timeout)
        with patch.object(bench, "_docker", side_effect=fake):
            self.assertIn("indirilemedi", bench.ensure_image(pull_timeout=5))

    def test_pull_does_not_count_against_run_timeout(self):
        calls = []

        def fake(*args, timeout):
            calls.append((args[0], timeout))
            return MagicMock(returncode=0 if args[0] == "pull" else 1, stderr="")
        with patch.object(bench, "_docker", side_effect=fake):
            bench.ensure_image(pull_timeout=300)
        self.assertIn(("pull", 300), calls)


class RunTests(unittest.TestCase):
    def _run(self, process):
        with patch.object(bench.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(bench, "ensure_image", return_value=None), \
                patch.object(bench.subprocess, "Popen", return_value=process), \
                patch.object(bench, "_kill") as kill:
            result = bench.benchmark(SRC, SRC, "f", options=bench.BenchmarkOptions(timeout=1))
        return result, kill

    def test_success_parses_last_json_line(self):
        process = MagicMock(returncode=0)
        process.communicate.return_value = ('noise\n{"ok": true, "before": {}, "after": {}}\n', "")
        result, kill = self._run(process)
        self.assertTrue(result["ok"])
        kill.assert_not_called()

    def test_timeout_kills_named_container(self):
        process = MagicMock(returncode=None)
        process.communicate.side_effect = [subprocess.TimeoutExpired("docker", 1), ("", "")]
        result, kill = self._run(process)
        self.assertFalse(result["ok"])
        self.assertIn("durduruldu", result["error"])
        process.kill.assert_called_once()
        kill.assert_called_once()
        self.assertTrue(kill.call_args.args[0].startswith("cqs-bench-"))

    def test_container_failure_reported(self):
        process = MagicMock(returncode=125)
        process.communicate.return_value = ("", "boom")
        result, _ = self._run(process)
        self.assertIn("boom", result["error"])


class RunnerScriptTests(unittest.TestCase):
    """Run the container script locally on trusted fixtures to check its measurement logic."""

    def test_time_and_memory_measured_separately_and_args_copied_outside_timer(self):
        source = bench.RUNNER
        self.assertIn("def measure_time", source)
        self.assertIn("def measure_memory", source)
        timed = source[source.index("def measure_time"):source.index("def measure_memory")]
        self.assertNotIn("tracemalloc", timed)
        self.assertLess(timed.index("fresh_args()"), timed.index("perf_counter_ns"))


BEFORE = "def build(n):\n    r = []\n    for i in range(n):\n        r = r + [i]\n    return r\n"
AFTER = "def build(n):\n    return list(range(n))\n"


class LocalModeTests(unittest.TestCase):
    """Yerel yedek mod: güvenilir sabit kodlarla gerçekten bir alt süreç çalıştırır."""

    def test_local_run_measures_and_is_equivalent(self):
        result = bench.benchmark(BEFORE, AFTER, "build", [500], {}, bench.BenchmarkOptions(repeats=3, mode="yerel"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "yerel")
        self.assertIn("median_ms", result["before"])
        self.assertIn("median_peak_bytes", result["after"])

    def test_different_outputs_are_rejected(self):
        result = bench.benchmark(BEFORE, "def build(n):\n    return []\n", "build", [5], {},
                                 bench.BenchmarkOptions(repeats=3, mode="yerel"))
        self.assertFalse(result["ok"])
        self.assertIn("farklı", result["error"])

    def test_timeout_stops_process(self):
        result = bench.benchmark("def f():\n    while True:\n        pass\n", "def f():\n    return 1\n", "f", [], {},
                                 bench.BenchmarkOptions(repeats=3, timeout=2, mode="yerel"))
        self.assertFalse(result["ok"])
        self.assertIn("durduruldu", result["error"])

    def test_syntax_error_and_bad_mode_are_reported_in_turkish(self):
        syntax = bench.benchmark(BEFORE, "def (", "build", [], {}, bench.BenchmarkOptions(mode="yerel"))
        self.assertIn("Sözdizimi", syntax["error"])
        self.assertIn("Geçersiz mod", bench.benchmark(BEFORE, AFTER, "build", [],
                                                      options=bench.BenchmarkOptions(mode="x"))["error"])

    def test_local_command_runs_in_isolated_mode_without_docker(self):
        command = bench._local_command(bench.Path("/tmp/x"))
        self.assertIn("-I", command)
        self.assertNotIn("docker", command)


class SummaryAndStatusTests(unittest.TestCase):
    def _result(self, t_before, t_after, m_before=1000, m_after=1000):
        return {"ok": True, "before": {"median_ms": t_before, "median_peak_bytes": m_before},
                "after": {"median_ms": t_after, "median_peak_bytes": m_after}}

    def test_faster_slower_and_noise(self):
        self.assertEqual(bench.summarize(self._result(10, 1))["time_verdict"], "Sonra sürümü daha hızlı")
        self.assertEqual(bench.summarize(self._result(1, 10))["time_verdict"], "Sonra sürümü daha yavaş")
        self.assertEqual(bench.summarize(self._result(1.00, 0.97))["time_verdict"], "belirgin fark yok")

    def test_memory_verdict_and_failed_result(self):
        self.assertIn("daha az bellek", bench.summarize(self._result(1, 1, 2000, 500))["memory_verdict"])
        self.assertFalse(bench.summarize({"ok": False})["ok"])

    def test_status_without_docker_cli(self):
        with patch.object(bench.shutil, "which", return_value=None):
            self.assertEqual(bench.docker_status(), {"cli": False, "daemon": False, "image": False})

    def test_status_with_running_daemon_and_image(self):
        with patch.object(bench.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(bench, "_docker", return_value=MagicMock(returncode=0)):
            self.assertEqual(bench.docker_status(), {"cli": True, "daemon": True, "image": True})

    def test_status_with_stopped_daemon(self):
        with patch.object(bench.shutil, "which", return_value="/usr/bin/docker"), \
                patch.object(bench, "_docker", return_value=MagicMock(returncode=1)):
            status = bench.docker_status()
        self.assertTrue(status["cli"])
        self.assertFalse(status["daemon"])


if __name__ == "__main__":
    unittest.main()
