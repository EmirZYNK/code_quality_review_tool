import unittest

from quality_studio.config import Config
from quality_studio.engine import analyze

from .helpers import rules


class RuleTableTests(unittest.TestCase):
    """(rule, code that must trigger it, code that must not)."""

    TABLE = [
        ("SEC101", "def _f(s):\n    return eval(s)\n", "import ast\nast.literal_eval(s)\n"),
        ("SEC102", "import pickle\npickle.loads(b)\n", "import json\njson.loads(b)\n"),
        ("SEC103", "import subprocess\nsubprocess.run(c, shell=True)\n", "import subprocess\nsubprocess.run([c])\n"),
        ("SEC104", "def _f(cur, x):\n    cur.execute('SELECT ' + x)\n", "def _f(cur, x):\n    cur.execute('SELECT ?', (x,))\n"),
        ("SEC105", "password = 'abc'\n", "token_url = 'https://x'\n"),
        ("SEC106", "import os\nos.system(c)\n", "import os\nos.getcwd()\n"),
        ("SEC107", "import yaml\nyaml.load(s)\n", "import yaml\nyaml.safe_load(s)\n"),
        ("SEC108", "import hashlib\nhashlib.md5(b)\n", "import hashlib\nhashlib.sha256(b)\n"),
        ("SEC109", "import requests\nrequests.get(u, verify=False)\n", "import requests\nrequests.get(u)\n"),
        ("SEC110", "assert x\n", "if not x:\n    raise ValueError\n"),
        ("SEC111", "import tempfile\ntempfile.mktemp()\n", "import tempfile\ntempfile.mkstemp()\n"),
        ("CQ104", "x == None\n", "x is None\n"),
        ("CQ105", "def _f(a=[]):\n    return a\n", "def _f(a=None):\n    return a\n"),
        ("CQ106", "x == True\n", "x is True\n"),
        ("CQ201", "try:\n    f()\nexcept:\n    g()\n", "try:\n    f()\nexcept OSError:\n    g()\n"),
        ("CQ202", "try:\n    f()\nexcept OSError:\n    pass\n", "try:\n    f()\nexcept OSError:\n    g()\n"),
        ("RED001", "import os\n", "import os\nos.getcwd()\n"),
        ("RED002", "def _f():\n    a = 1\n    return 2\n", "def _f():\n    a = 1\n    return a\n"),
        ("RED005", "def _f():\n    return 1\n    g()\n", "def _f():\n    g()\n    return 1\n"),
        ("RED006", "def _f():\n    g()\n    pass\n", "def _f():\n    pass\n"),
        ("PERF103", "r = []\nfor i in s:\n    r = r + [i]\n", "r = []\nfor i in s:\n    r.append(i)\n"),
    ]

    def test_trigger_and_clean(self):
        for rule, bad, good in self.TABLE:
            with self.subTest(rule=rule):
                self.assertIn(rule, rules(bad))
                self.assertNotIn(rule, rules(good))


class ReviewRegressionTests(unittest.TestCase):
    """Each case below was reported as a real defect in the first version."""

    def test_sql_call_argument_is_not_flagged(self):
        self.assertNotIn("SEC104", rules("def _f(cur):\n    cur.execute(build_query())\n"))

    def test_sql_constant_concatenation_is_not_flagged(self):
        self.assertNotIn("SEC104", rules("def _f(cur):\n    cur.execute('SELECT 1' + ' FROM t')\n"))

    def test_sql_built_in_variable_is_flagged(self):
        code = "def _f(cur, x):\n    q = f'SELECT {x}'\n    cur.execute(q)\n"
        self.assertIn("SEC104", rules(code))

    def test_count_on_string_is_not_a_list_finding(self):
        self.assertNotIn("PERF102", rules("t = 'abc'\nfor c in s:\n    print(t.count(c))\n"))

    def test_credential_false_positives_and_misses(self):
        self.assertNotIn("SEC105", rules("token_url = 'https://x'\n"))
        self.assertNotIn("SEC105", rules("max_tokens = '5'\n"))
        for code in ("class A:\n    def __init__(self):\n        self.password = 'abc'\n",
                     "password: str = 'abc'\n",
                     "cfg = {'password': 'abc'}\n",
                     "def _f(password='abc'):\n    return password\n"):
            with self.subTest(code=code):
                self.assertIn("SEC105", rules(code))

    def test_for_while_and_comprehension_nesting_counted(self):
        triple_for_while = "for a in x:\n    while a:\n        for b in y:\n            print(b)\n"
        triple_comprehension = "m = [[a * b for b in y] for a in x]\nn = [[[1 for c in z] for b in y] for a in x]\n"
        self.assertIn("PERF101", rules(triple_for_while))
        self.assertIn("PERF101", rules(triple_comprehension))

    def test_grid_traversal_is_not_noise(self):
        self.assertNotIn("PERF101", rules("for row in rows:\n    for cell in row:\n        print(cell)\n"))

    def test_long_function_counts_lines_not_statements(self):
        one_statement = "def _f():\n    return [\n" + "".join(f"        {i},\n" for i in range(80)) + "    ]\n"
        many_simple = "def _g():\n" + "".join(f"    print({i})\n" for i in range(41))
        self.assertIn("CQ101", rules(one_statement))
        self.assertNotIn("CQ101", rules(many_simple))

    def test_elif_chain_is_not_deep_nesting(self):
        code = "def _f(x):\n" + "    if x == 0:\n        return 0\n" + "".join(
            f"    elif x == {i}:\n        return {i}\n" for i in range(1, 8))
        self.assertNotIn("CQ108", rules(code))

    def test_missing_security_rules_now_present(self):
        for rule, code in (("SEC107", "import yaml\nyaml.load(s)\n"),
                           ("SEC108", "import hashlib\nhashlib.md5(b)\n"),
                           ("SEC109", "import requests\nrequests.get(u, verify=False)\n"),
                           ("SEC110", "assert ok\n"),
                           ("CQ105", "def _f(a=[]):\n    pass\n"),
                           ("CQ106", "x == True\n"),
                           ("SEC111", "import tempfile\ntempfile.mktemp()\n")):
            with self.subTest(rule=rule):
                self.assertIn(rule, rules(code))


class ScoreAndMetricTests(unittest.TestCase):
    def test_score_does_not_saturate(self):
        ten = analyze("\n".join(["eval(x)"] * 10)).metrics["score"]
        twenty = analyze("\n".join(["eval(x)"] * 20)).metrics["score"]
        self.assertGreater(ten, twenty)
        self.assertGreater(ten, 0)

    def test_score_depends_on_size(self):
        short = analyze("eval(x)\n").metrics["score"]
        long = analyze("eval(x)\n" + "y = 1\nprint(y)\n" * 200).metrics["score"]
        self.assertGreater(long, short)

    def test_clean_code_scores_100(self):
        self.assertEqual(analyze("print(1)\n").metrics["score"], 100)

    def test_ai_indicators_do_not_change_score(self):
        code = "def _f(t):\n    r = t.strip()\n    return r\n"
        result = analyze(code)
        self.assertIn("AIP001", {f.rule for f in result.findings})
        self.assertEqual(result.metrics["score"], 100)

    def test_single_complexity_definition(self):
        code = "def _f(a, b):\n    if a and b:\n        return [x for x in a if x]\n    return 0\n"
        metrics = analyze(code).metrics
        # 1 + if + and + comprehension + its if
        self.assertEqual(metrics["complexity_max_function"], 5)
        self.assertEqual(metrics["complexity_total"], 5)


class SuppressionAndConfigTests(unittest.TestCase):
    def test_noqa_variants(self):
        self.assertNotIn("CQ104", rules("x == None  # noqa\n"))
        self.assertNotIn("CQ104", rules("x == None  # noqa: CQ104\n"))
        self.assertNotIn("CQ104", rules("x == None  # cqs: ignore[CQ104]\n"))
        self.assertIn("CQ104", rules("x == None  # noqa: SEC101\n"))

    def test_suppressed_count(self):
        self.assertEqual(analyze("x == None  # noqa\n").suppressed, 1)

    def test_disabled_rule(self):
        self.assertNotIn("CQ104", rules("x == None\n", config=Config(disabled=frozenset({"CQ104"}))))

    def test_threshold_override(self):
        code = "def _f(a, b, c):\n    return a + b + c\n"
        self.assertIn("CQ102", rules(code, config=Config(max_parameters=2)))
        self.assertNotIn("CQ102", rules(code))

    def test_unknown_config_key(self):
        with self.assertRaises(ValueError):
            Config.from_mapping({"nope": 1})

    def test_syntax_error_and_size_limit(self):
        result = analyze("def broken(:\n")
        self.assertFalse(result.valid)
        self.assertEqual(result.findings[0].rule, "SYN001")
        with self.assertRaises(ValueError):
            analyze("x = 1\n" * 100_000)


class RedundancyAndAiTests(unittest.TestCase):
    def test_duplicate_function(self):
        body = "    t = 0\n    for i in xs:\n        if i > 0:\n            t += i * 2\n    return t\n"
        self.assertIn("RED004", rules(f"def _a(xs):\n{body}\ndef _b(xs):\n{body}"))

    def test_ai_rules_are_info_severity(self):
        result = analyze("def _f(t):\n    r = t.strip()\n    return r\n")
        self.assertTrue(all(f.severity == "info" for f in result.findings if f.category == "AI Pattern"))

    def test_restating_comments_need_several(self):
        one = "# Initialize the list\nx = []\n"
        many = "# Initialize the list\nx = []\n# Loop through x\nfor i in x:\n    # Append the item\n    x.append(i)\n"
        self.assertNotIn("AIP003", rules(one))
        self.assertIn("AIP003", rules(many))


if __name__ == "__main__":
    unittest.main()
