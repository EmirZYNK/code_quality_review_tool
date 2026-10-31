"""RED007: gereksiz if/else ile True/False döndürme."""
import unittest

from .helpers import rules


class Red007Tests(unittest.TestCase):
    def test_if_else_true_false_is_flagged(self):
        code = "def _f(x):\n    if x:\n        return True\n    else:\n        return False\n"
        self.assertIn("RED007", rules(code))

    def test_inverse_false_true_is_flagged(self):
        code = "def _f(x):\n    if x > 3:\n        return False\n    else:\n        return True\n"
        self.assertIn("RED007", rules(code))

    def test_reports_the_line_of_the_if(self):
        from quality_studio.engine import analyze

        code = "def _f(x):\n    y = 1\n    if x:\n        return True\n    else:\n        return False\n"
        findings = [f for f in analyze(code).findings if f.rule == "RED007"]
        self.assertEqual([f.line for f in findings], [3])

    def test_message_suggests_bool_form(self):
        from quality_studio.engine import analyze

        code = "def _f(x):\n    if x > 3:\n        return True\n    else:\n        return False\n"
        finding = next(f for f in analyze(code).findings if f.rule == "RED007")
        self.assertIn("return bool(x > 3)", finding.message)

    def test_non_bool_return_values_are_not_flagged(self):
        code = "def _f(x):\n    if x:\n        return 1\n    else:\n        return 0\n"
        self.assertNotIn("RED007", rules(code))

    def test_same_value_in_both_branches_is_not_flagged(self):
        code = "def _f(x):\n    if x:\n        return True\n    else:\n        return True\n"
        self.assertNotIn("RED007", rules(code))

    def test_extra_statement_in_branch_is_not_flagged(self):
        code = "def _f(x):\n    if x:\n        print('yes')\n        return True\n    else:\n        return False\n"
        self.assertNotIn("RED007", rules(code))

    def test_assignment_instead_of_return_is_not_flagged(self):
        code = "def _f(x):\n    if x:\n        ok = True\n    else:\n        ok = False\n    return ok\n"
        self.assertNotIn("RED007", rules(code))

    def test_elif_chain_with_other_values_is_not_flagged(self):
        code = "def _f(x, y):\n    if x:\n        return True\n    elif y:\n        return False\n    else:\n        return None\n"
        self.assertNotIn("RED007", rules(code))

    def test_can_be_disabled(self):
        from quality_studio.config import Config

        code = "def _f(x):\n    if x:\n        return True\n    else:\n        return False\n"
        self.assertNotIn("RED007", rules(code, config=Config(disabled=frozenset({"RED007"}))))

    def test_can_be_suppressed_with_noqa(self):
        code = "def _f(x):\n    if x:  # noqa: RED007\n        return True\n    else:\n        return False\n"
        self.assertNotIn("RED007", rules(code))


if __name__ == "__main__":
    unittest.main()
