import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from quality_studio import external
from quality_studio.cli import main
from quality_studio.codeview import render_code
from quality_studio.engine import analyze
from quality_studio.models import Finding
from quality_studio.project_scan import project_summary, scan_archive, scan_directory
from quality_studio.report import to_html, to_json, to_sarif, to_text


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class ProjectScanTests(unittest.TestCase):
    def test_multiple_files_and_summary(self):
        files = scan_archive(make_zip({"a.py": "x = eval('1')\n", "src/b.py": "def foo():\n    pass\n"}))
        summary = project_summary(files)
        self.assertEqual(summary["files"], 2)
        self.assertGreaterEqual(summary["issues"], 1)
        self.assertGreaterEqual(summary["high_priority"], 1)

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "No .py"):
            scan_archive(make_zip({"README.md": "hi"}))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            scan_archive(make_zip({"../bad.py": "x=1"}))
        with self.assertRaisesRegex(ValueError, "too large"):
            scan_archive(make_zip({"big.py": "x" * 250_001}))
        with self.assertRaisesRegex(ValueError, "Invalid"):
            scan_archive(b"not a zip")

    def test_pycache_and_venv_are_skipped(self):
        files = scan_archive(make_zip({"a.py": "x = 1\n", "__pycache__/a.py": "x=1", ".venv/lib/z.py": "x=1"}))
        self.assertEqual([f["path"] for f in files], ["a.py"])

    def test_bad_encoding_skips_one_file_not_the_scan(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("good.py", "x = 1\n")
            archive.writestr("bad.py", b"\xff\xfe\x00bad")
        summary = project_summary(scan_archive(buffer.getvalue()))
        self.assertEqual((summary["analyzed_files"], summary["skipped_files"]), (1, 1))

    def test_directory_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "m.py").write_text("x = eval('1')\n", encoding="utf-8")
            (Path(folder) / "__pycache__").mkdir()
            (Path(folder) / "__pycache__" / "m.py").write_text("x=1", encoding="utf-8")
            files = scan_directory(Path(folder))
        self.assertEqual([f["path"] for f in files], ["m.py"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.entries = [("pkg/a.py", analyze("def _f(s):\n    return eval(s)\n"))]

    def test_sarif_structure(self):
        sarif = json.loads(to_sarif(self.entries))
        self.assertEqual(sarif["version"], "2.1.0")
        run = sarif["runs"][0]
        rule_ids = [r["id"] for r in run["tool"]["driver"]["rules"]]
        result = run["results"][0]
        self.assertEqual(result["ruleId"], "SEC101")
        self.assertEqual(rule_ids[result["ruleIndex"]], "SEC101")
        self.assertEqual(result["level"], "error")
        location = result["locations"][0]["physicalLocation"]
        self.assertEqual(location["artifactLocation"]["uri"], "pkg/a.py")
        self.assertEqual(location["region"]["startLine"], 2)
        sec101 = run["tool"]["driver"]["rules"][rule_ids.index("SEC101")]
        self.assertIn("external/cwe/cwe-95", sec101["properties"]["tags"])

    def test_json_and_text(self):
        data = json.loads(to_json(self.entries))
        self.assertFalse(data["source_executed"])
        self.assertEqual(data["files"][0]["findings"][0]["rule"], "SEC101")
        self.assertIn("pkg/a.py:2: SEC101", to_text(self.entries))

    def test_html_is_escaped(self):
        code = 'x = "<script>alert(1)</script>"\n'
        page = to_html((code, analyze(code)))
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_code_view_escapes(self):
        output = render_code('x = "<script>alert(1)</script>"')
        self.assertNotIn("<script>", output)
        issue = {"line": 1, "severity": "low", "rule": "X", "message": '" onmouseover="alert(1)'}
        self.assertIn("&quot; onmouseover=&quot;", render_code("pass", [issue]))

    def test_code_view_highlight_and_focus(self):
        issue = {"line": 2, "severity": "high", "rule": "T1", "message": "test"}
        output = render_code("a = 1\nb = 2", [issue], focus_line=2)
        self.assertIn("T1: test", output)
        self.assertIn("rgba(96,165,250,.14)", output)
        self.assertIn("border-left:3px solid #fb7185", output)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write(self, folder, name, text):
        path = Path(folder) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = self.write(folder, "bad.py", "x = eval('1')\n")
            clean = self.write(folder, "ok.py", "print(1)\n")
            self.assertEqual(self.run_cli(bad)[0], 1)
            self.assertEqual(self.run_cli(clean)[0], 0)
            self.assertEqual(self.run_cli(str(Path(folder) / "missing.py"))[0], 2)

    def test_fail_on_threshold(self):
        with tempfile.TemporaryDirectory() as folder:
            low = self.write(folder, "low.py", "x == None\n")
            self.assertEqual(self.run_cli(low, "--fail-on", "medium")[0], 0)
            self.assertEqual(self.run_cli(low, "--fail-on", "low")[0], 1)

    def test_disable_and_unknown_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = self.write(folder, "bad.py", "x = eval('1')\n")
            self.assertEqual(self.run_cli(bad, "--disable", "SEC101")[0], 0)
            code, _, err = self.run_cli(bad, "--disable", "NOPE")
            self.assertEqual(code, 2)
            self.assertIn("NOPE", err)

    def test_formats_and_zip_and_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            self.write(folder, "bad.py", "x = eval('1')\n")
            archive = Path(folder) / "p.zip"
            archive.write_bytes(make_zip({"bad.py": "x = eval('1')\n"}))
            for target in (folder, str(archive)):
                code, out, _ = self.run_cli(target, "--format", "sarif")
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(out)["version"], "2.1.0")
            code, out, _ = self.run_cli(str(Path(folder) / "bad.py"), "--format", "json")
            self.assertEqual(json.loads(out)["files"][0]["path"], "bad.py")

    def test_multiple_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self.write(folder, "a.py", "x = eval('1')\n")
            second = self.write(folder, "b.py", "print(1)\n")
            code, out, _ = self.run_cli(first, second, "--format", "json")
            self.assertEqual(code, 1)
            self.assertEqual([f["path"] for f in json.loads(out)["files"]], ["a.py", "b.py"])

    def test_config_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.write(folder, "f.py", "def _f(a, b, c):\n    return a + b + c\n")
            config = self.write(folder, "c.json", json.dumps({"max_parameters": 2}))
            self.assertEqual(self.run_cli(source, "--config", config, "--fail-on", "low")[0], 1)
            bad_config = self.write(folder, "bad.json", json.dumps({"bogus": 1}))
            self.assertEqual(self.run_cli(source, "--config", bad_config)[0], 2)


class ExternalToolTests(unittest.TestCase):
    RUFF = json.dumps([{"code": "S307", "message": "eval", "location": {"row": 2, "column": 12},
                        "end_location": {"row": 2, "column": 19}}, {"code": "F821", "message": "undefined name",
                        "location": {"row": 5, "column": 1}, "end_location": {"row": 5, "column": 4}}])
    BANDIT = json.dumps({"results": [{"test_id": "B307", "issue_text": "eval used", "issue_severity": "MEDIUM",
                                      "issue_confidence": "HIGH", "line_number": 2, "issue_cwe": {"id": 78}}]})

    def test_parsers(self):
        ruff = external.parse_ruff(self.RUFF)
        self.assertEqual((ruff[0].rule, ruff[0].category, ruff[0].line), ("RUFF:S307", "Security", 2))
        bandit = external.parse_bandit(self.BANDIT)
        self.assertEqual((bandit[0].rule, bandit[0].severity, bandit[0].cwe), ("BANDIT:B307", "medium", "CWE-78"))

    def test_merge_corroborates_and_keeps_unmatched(self):
        ours = analyze("def _f(s):\n    return eval(s)\n").findings
        extra = external.parse_ruff(self.RUFF) + external.parse_bandit(self.BANDIT)
        merged = external.merge(ours, extra)
        sec = next(f for f in merged if f.rule == "SEC101")
        self.assertEqual(sec.corroborated_by, ("bandit:B307", "ruff:S307"))
        self.assertTrue(any(f.rule == "RUFF:F821" for f in merged))
        self.assertFalse(any(f.rule in ("RUFF:S307", "BANDIT:B307") for f in merged))

    def test_tools_missing_is_reported_not_fatal(self):
        with patch.object(external.shutil, "which", return_value=None):
            findings, status = external.run_external("x = 1\n")
        self.assertEqual(findings, [])
        self.assertEqual(status, {"ruff": "not installed", "bandit": "not installed"})

    def test_run_uses_argument_list_without_shell(self):
        completed = type("P", (), {"stdout": "[]", "stderr": "", "returncode": 0})()
        with patch.object(external.shutil, "which", side_effect=lambda n: "/bin/ruff" if n == "ruff" else None), \
                patch.object(external.subprocess, "run", return_value=completed) as run:
            _, status = external.run_external("x = 1\n")
        self.assertEqual(status["ruff"], "ok")
        command = run.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_engine_external_flag_adds_status(self):
        with patch.object(external.shutil, "which", return_value=None):
            result = analyze("x = 1\n", external=True)
        self.assertEqual(result.external["ruff"], "not installed")

    def test_finding_is_immutable(self):
        finding = Finding("X", "Style", "low", 1, 1, "m", "w", "f")
        with self.assertRaises(AttributeError):
            finding.line = 2


if __name__ == "__main__":
    unittest.main()
