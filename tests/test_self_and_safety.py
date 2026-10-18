import ast
import tempfile
import unittest
from pathlib import Path

from quality_studio.engine import analyze

ROOT = Path(__file__).resolve().parent.parent


class SelfAnalysisTests(unittest.TestCase):
    """The tool must hold itself to its own standard."""

    def test_project_sources_have_no_medium_or_higher_findings(self):
        offenders = []
        for path in sorted([*ROOT.joinpath("quality_studio").rglob("*.py"), ROOT / "app.py"]):
            for finding in analyze(path.read_text(encoding="utf-8")).findings:
                if finding.severity in ("medium", "high", "critical"):
                    offenders.append(f"{path.name}:{finding.line} {finding.rule}")
        self.assertEqual(offenders, [])

    def test_one_statement_per_line_and_no_stale_files(self):
        for path in ROOT.joinpath("quality_studio").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if isinstance(body, list):
                    lines = [stmt.lineno for stmt in body if isinstance(stmt, ast.stmt)]
                    self.assertEqual(len(lines), len(set(lines)), f"{path.name}: several statements on one line")
        for stale in ("formatter.py", "client.py", "transformer_legacy.py", "sandbox.py"):
            self.assertEqual(list(ROOT.rglob(stale)), [])
        self.assertFalse((ROOT / ".env").exists())


class SafetyTests(unittest.TestCase):
    def test_analysis_never_executes_source(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "danger.txt"
            analyze(f"open({str(target)!r}, 'w').write('bad')\n")
            self.assertFalse(target.exists())

    def test_deeply_nested_source_fails_cleanly(self):
        deep = "x = " + "(" * 400 + "1" + ")" * 400 + "\n"
        try:
            result = analyze(deep)
        except ValueError:
            return
        self.assertIn(result.valid, (True, False))


if __name__ == "__main__":
    unittest.main()
