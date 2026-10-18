import unittest

from quality_studio.diffview import classify, render_side_by_side
from quality_studio.engine import analyze
from quality_studio.impact import patch_effects
from quality_studio.refactor import apply, suggest

BEFORE = "def f(x):\n    return x == None\n"
AFTER = "def f(x):\n    return x is None\n"


class DiffViewTests(unittest.TestCase):
    def test_resolved_finding_is_marked_on_left_only(self):
        before, after = analyze(BEFORE).issues, analyze(AFTER).issues
        resolved, introduced = classify(before, after)
        self.assertEqual(len(resolved), 1)
        self.assertFalse(introduced)
        html = render_side_by_side(BEFORE, AFTER, before, after)
        self.assertIn("chip resolved", html)
        self.assertNotIn("chip new", html)

    def test_new_finding_is_marked_on_right(self):
        before, after = analyze(AFTER).issues, analyze(BEFORE).issues
        html = render_side_by_side(AFTER, BEFORE, before, after)
        self.assertIn("chip new", html)

    def test_source_is_escaped(self):
        code = "x = '<script>alert(1)</script>'\n"
        html = render_side_by_side(code, code)
        self.assertNotIn("<script>alert(1)", html)

    def test_identical_versions_have_no_changed_rows(self):
        html = render_side_by_side(BEFORE, BEFORE)
        self.assertNotIn('<tr class="changed">', html)


class PatchEffectTests(unittest.TestCase):
    def test_each_patch_reports_its_own_effect(self):
        code = "def f(x):\n    try:\n        return x == None\n    except:\n        pass\n"
        patches = suggest(code)
        effects = patch_effects(code, patches)
        self.assertEqual(len(effects), len(patches))
        for index, effect in enumerate(effects):
            self.assertEqual(effect["score_after"], analyze(apply(code, patches, [index])).metrics["score"])


if __name__ == "__main__":
    unittest.main()
