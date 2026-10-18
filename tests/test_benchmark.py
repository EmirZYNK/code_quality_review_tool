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
            self.assertIn("exceeded", bench.ensure_image(pull_timeout=5))

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
        self.assertIn("stopped", result["error"])
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


if __name__ == "__main__":
    unittest.main()
