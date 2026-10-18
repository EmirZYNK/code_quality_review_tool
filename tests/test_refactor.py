import ast
import tempfile
import unittest
from pathlib import Path

from quality_studio.engine import analyze
from quality_studio.impact import impact
from quality_studio.refactor import apply, suggest, unified_diff


class PatchTests(unittest.TestCase):
    def test_none_comparison_patch(self):
        code = "def f(x):\n    return x == None\n"
        patches = suggest(code)
        self.assertEqual(len(patches), 1)
        after = apply(code, patches, [0])
        self.assertIn("x is None", after)
        self.assertIn("-    return x == None", unified_diff(code, after))

    def test_bare_except_patch_keeps_comment(self):
        code = "try:\n    pass\nexcept:  # comment\n    pass\n"
        patched = apply(code, suggest(code), [0])
        self.assertIn("except Exception:  # comment", patched)
        ast.parse(patched)

    def test_yaml_load_patch(self):
        code = "import yaml\ndata = yaml.load(text)\n"
        patched = apply(code, suggest(code), [0])
        self.assertIn("yaml.safe_load(text)", patched)

    def test_non_ascii_text_does_not_corrupt_patch(self):
        """ast reports byte columns; the old patcher used them as character indexes."""
        code = "x = 'é'; y = a == None\n"
        patched = apply(code, suggest(code), [0])
        self.assertEqual(patched, "x = 'é'; y = a is None\n")

    def test_turkish_characters_and_multiple_patches(self):
        code = "ad = 'Şükrü'; sonuc = (veri != None) and (öğe == None)\n"
        patches = suggest(code)
        self.assertEqual(len(patches), 2)
        patched = apply(code, patches, range(len(patches)))
        self.assertEqual(patched, "ad = 'Şükrü'; sonuc = (veri is not None) and (öğe is None)\n")

    def test_crlf_and_form_feed_line_numbers(self):
        code = "a = 1\r\n\x0c\r\nb = c == None\r\n"
        patched = apply(code, suggest(code), [0])
        self.assertEqual(patched, "a = 1\r\n\x0c\r\nb = c is None\r\n")

    def test_overlap_and_stale_patches_rejected(self):
        code = "x = a == None\n"
        patches = suggest(code)
        with self.assertRaises(ValueError):
            apply("x = b == None\n", patches, [0])
        overlapping = [dict(patches[0]), dict(patches[0])]
        with self.assertRaises(ValueError):
            apply(code, overlapping, [0, 1])

    def test_never_executes_code(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "oops"
            code = f"open({str(target)!r}, 'w').write('x')\nif 1 == None:\n    pass\n"
            patches = suggest(code)
            apply(code, patches, range(len(patches)))
            self.assertFalse(target.exists())

    def test_empty_and_unselected(self):
        self.assertEqual(suggest("x = 1\n"), [])
        self.assertEqual(apply("x = 1\n", [], []), "x = 1\n")


class ImpactTests(unittest.TestCase):
    def test_resolved_issue(self):
        data = impact("def f(x):\n    return x == None\n", "def f(x):\n    return x is None\n")
        self.assertTrue(data["valid"])
        self.assertGreaterEqual(data["resolved"], 1)
        self.assertFalse(data["runtime"]["measured"])

    def test_invalid_after(self):
        self.assertFalse(impact("x = 1\n", "def broken(")["valid"])

    def test_introduced_issue(self):
        data = impact("x = 1\n", "x = eval('1+2')\n")
        self.assertGreaterEqual(data["introduced"], 1)
        self.assertLess(data["score_delta"], 0)

    def test_patch_improves_score(self):
        code = "x = a == None\n"
        patched = apply(code, suggest(code), [0])
        self.assertGreater(analyze(patched).metrics["score"], analyze(code).metrics["score"])


if __name__ == "__main__":
    unittest.main()
