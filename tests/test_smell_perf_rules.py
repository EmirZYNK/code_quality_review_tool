"""Kişi 2 testleri: PERF101 hata düzeltmesi (regresyon), CQ109, CQ110 ve PERF104."""
import unittest
from pathlib import Path

from quality_studio.config import Config

from .helpers import rules

ROOT = Path(__file__).resolve().parent.parent


class NestedLoopRegressionTests(unittest.TestCase):
    """examples/sample_target.py iç içe for döngüsü içeriyordu ama PERF101 bulgu vermiyordu.

    Neden: PERF101 varsayılan eşiği 3 (min_loop_nest_depth) idi ve bu örnek 2 seviyeli, tüm-çiftler
    (aynı koleksiyon) kalıbında da değildi. Bu yüzden hiçbir koşul çalışmıyordu.
    """

    def test_sample_target_reports_nested_loop(self):
        code = (ROOT / "examples" / "sample_target.py").read_text(encoding="utf-8")
        self.assertIn("PERF101", rules(code))

    def test_two_independent_for_loops_are_reported(self):
        self.assertIn("PERF101", rules("for n in nums:\n    for i in range(100):\n        print(n * i)\n"))
        self.assertIn("PERF101", rules("for a in xs:\n    for b in ys:\n        print(a, b)\n"))

    def test_fix_does_not_create_false_alarms(self):
        quiet = {
            "grid": "for row in rows:\n    for cell in row:\n        print(cell)\n",
            "linked_list": "for item in items:\n    while item:\n        item = item.next\n",
            "matrix_comprehension": "g = [[i * j for j in range(w)] for i in range(h)]\nprint(g)\n",
            "small_range": "for n in nums:\n    for i in range(5):\n        print(n * i)\n",
            "depends_on_outer": "for n in nums:\n    for i in range(n):\n        print(i)\n",
            "derived_from_outer": "for item in items:\n    rows = load(item)\n    for row in rows:\n        print(row)\n",
        }
        for name, code in quiet.items():
            with self.subTest(case=name):
                self.assertNotIn("PERF101", rules(code))

    def test_high_threshold_silences_depth_two(self):
        code = "for a in xs:\n    for b in ys:\n        print(a, b)\n"
        self.assertNotIn("PERF101", rules(code, config=Config(min_loop_nest_depth=4)))


class LongLineTests(unittest.TestCase):
    def test_long_line_is_reported_at_default_limit(self):
        self.assertIn("CQ109", rules("x = '" + "a" * 130 + "'\nprint(x)\n"))

    def test_line_exactly_at_limit_is_fine(self):
        line = "x = '" + "a" * 114 + "'"
        self.assertEqual(len(line), 120)
        self.assertNotIn("CQ109", rules(line + "\nprint(x)\n"))

    def test_threshold_is_configurable(self):
        code = "total = first_value + second_value + third_value + fourth_value\nprint(total)\n"
        self.assertNotIn("CQ109", rules(code))
        self.assertIn("CQ109", rules(code, config=Config(max_line_length=40)))
        self.assertEqual(Config.from_mapping({"max_line_length": 80}).max_line_length, 80)

    def test_unbreakable_url_is_not_reported(self):
        self.assertNotIn("CQ109", rules("# https://example.com/" + "a" * 150 + "\nprint(1)\n"))

    def test_unicode_counts_characters_not_bytes(self):
        text = "x = '" + "ş" * 100 + "'\nprint(x)\n"  # 106 karakter ama 206 bayt
        self.assertNotIn("CQ109", rules(text))

    def test_line_number_and_suppression(self):
        code = "print(1)\nx = '" + "a" * 130 + "'\n"
        self.assertIn("CQ109", rules(code))
        self.assertNotIn("CQ109", rules(code.rstrip("\n") + "  # noqa: CQ109\n"))


class PrintOnlyHandlerTests(unittest.TestCase):
    TABLE = [
        ("try:\n    f()\nexcept Exception as e:\n    print(e)\n", True),
        ("try:\n    f()\nexcept Exception:\n    print('a')\n    print('b')\n", True),
        ("try:\n    f()\nexcept (ValueError, Exception):\n    print('a')\n", True),
        ("try:\n    f()\nexcept Exception as e:\n    print(e)\n    raise\n", False),
        ("try:\n    f()\nexcept Exception as e:\n    cleanup()\n    print(e)\n", False),
        ("try:\n    f()\nexcept ValueError as e:\n    print(e)\n", False),
        ("try:\n    f()\nexcept Exception:\n    pass\n", False),
        ("try:\n    f()\nexcept Exception:\n    logging.exception('x')\n", False),
    ]

    def test_table(self):
        for code, expected in self.TABLE:
            with self.subTest(code=code):
                self.assertEqual("CQ110" in rules(code), expected)


class LoopHoistTests(unittest.TestCase):
    TABLE = [
        ("import re\nfor s in xs:\n    re.compile('a+').match(s)\n", True),
        ("for x in xs:\n    with open('log.txt', 'a') as f:\n        f.write(x)\n", True),
        ("while go():\n    open('state.txt').read()\n", True),
        ("import re as regex\nfor s in xs:\n    regex.compile('a')\n", True),
        ("from re import compile\nfor s in xs:\n    compile('a')\n", True),
        ("r = [open('a.txt') for _ in range(3)]\n", True),
        ("import re\np = re.compile('a')\nfor s in xs:\n    p.match(s)\n", False),
        ("for line in open('data.txt'):\n    print(line)\n", False),
        ("for path in paths:\n    open(path).read()\n", False),
        ("import re\nfor p in pats:\n    re.compile(p)\n", False),
        ("for d in dirs:\n    name = d + '.txt'\n    open(name).read()\n", False),
        ("for x in xs:\n    def read():\n        return open('a.txt').read()\n", False),
        ("open('a.txt').read()\n", False),
        ("from os import open\nfor x in xs:\n    open('a', 0)\n", False),
    ]

    def test_table(self):
        for code, expected in self.TABLE:
            with self.subTest(code=code):
                self.assertEqual("PERF104" in rules(code), expected)


if __name__ == "__main__":
    unittest.main()
