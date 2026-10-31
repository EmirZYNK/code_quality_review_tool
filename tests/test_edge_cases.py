"""Kenar durumlar: boş dosya, sadece yorum, Unicode, async def, match, lambda, dekoratör."""
import unittest

from quality_studio.engine import analyze

from .helpers import rules


def _duplicates(code: str) -> list:
    """Aynı (kural, satır) çiftinin birden fazla kez raporlandığı bulgular."""
    seen, repeated = set(), []
    for finding in analyze(code).findings:
        key = (finding.rule, finding.line)
        if key in seen:
            repeated.append(key)
        seen.add(key)
    return repeated


class EmptyAndCommentOnlyTests(unittest.TestCase):
    def test_empty_file_is_valid_and_clean(self):
        result = analyze("")
        self.assertTrue(result.valid)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.metrics["score"], 100)

    def test_whitespace_only_file_is_clean(self):
        result = analyze("\n\n   \n")
        self.assertTrue(result.valid)
        self.assertEqual(result.findings, [])

    def test_comment_only_file_is_clean(self):
        result = analyze("# sadece yorum\n# bir yorum daha\n")
        self.assertTrue(result.valid)
        self.assertEqual(result.findings, [])


class UnicodeTests(unittest.TestCase):
    CODE = "isim = 'Şükrü Çağlar 😀'\n\ndef _selamla(kişi):\n    return f'Merhaba {kişi}'\n"

    def test_unicode_source_is_clean(self):
        result = analyze(self.CODE)
        self.assertTrue(result.valid)
        self.assertEqual(result.findings, [])

    def test_unicode_identifier_is_still_checked(self):
        self.assertIn("RED002", rules("def _f():\n    değişken = 1\n    return 2\n"))

    def test_line_numbers_are_right_after_unicode_text(self):
        code = "metin = 'çğıöşü 😀'\nimport os\n"
        finding = next(f for f in analyze(code).findings if f.rule == "RED001")
        self.assertEqual(finding.line, 2)


class AsyncTests(unittest.TestCase):
    def test_clean_async_function_has_no_findings(self):
        code = "import asyncio\n\nasync def _bekle(n):\n    await asyncio.sleep(n)\n    return n\n"
        self.assertEqual(analyze(code).findings, [])

    def test_async_function_gets_function_rules(self):
        self.assertIn("RED002", rules("async def _f():\n    unused = 1\n    return 2\n"))
        self.assertIn("CQ103", rules("async def fetch():\n    return 1\n"))

    def test_async_for_and_with_do_not_crash(self):
        code = "async def _f(s):\n    async with s as c:\n        async for x in c:\n            print(x)\n"
        self.assertTrue(analyze(code).valid)

    def test_red007_works_in_async_function(self):
        code = "async def _f(x):\n    if x:\n        return True\n    else:\n        return False\n"
        self.assertIn("RED007", rules(code))


class MatchStatementTests(unittest.TestCase):
    def test_clean_match_has_no_findings(self):
        code = "def _f(x):\n    match x:\n        case 1:\n            return 'a'\n        case _:\n            return 'b'\n"
        self.assertEqual(analyze(code).findings, [])

    def test_dead_code_inside_case_is_reported(self):
        code = "def _f(x):\n    match x:\n        case 1:\n            return 1\n            print('never')\n        case _:\n            return 2\n"
        self.assertIn("RED005", rules(code))

    def test_match_findings_are_not_reported_twice(self):
        """Gerileme: case gövdeleri hem match düğümünden hem match_case düğümünden taranıyordu."""
        code = "def _f(x):\n    match x:\n        case 1:\n            return 1\n            print('never')\n        case _:\n            pass\n            print('x')\n"
        self.assertEqual(_duplicates(code), [])

    def test_red007_inside_case_is_flagged(self):
        code = ("def _f(x, y):\n    match x:\n        case 1:\n            if y:\n"
                "                return True\n            else:\n                return False\n        case _:\n            return None\n")
        self.assertIn("RED007", rules(code))


class LambdaTests(unittest.TestCase):
    def test_lambda_assignment_is_clean(self):
        self.assertEqual(analyze("kare = lambda x: x * x\nprint(kare(3))\n").findings, [])

    def test_lambda_parameters_are_not_unused_variables(self):
        self.assertNotIn("RED002", rules("def _f(xs):\n    return list(map(lambda y: y + 1, xs))\n"))

    def test_lambda_in_loop_does_not_crash(self):
        self.assertTrue(analyze("for i in range(3):\n    f = lambda: i\n    print(f())\n").valid)


class DecoratorTests(unittest.TestCase):
    def test_import_used_only_by_decorator_is_not_unused(self):
        code = "from functools import lru_cache\n\n@lru_cache(maxsize=None)\ndef _f(n):\n    return n\n"
        self.assertNotIn("RED001", rules(code))

    def test_decorated_function_still_needs_docstring(self):
        code = "import functools\n\n@functools.cache\ndef public(n):\n    return n\n"
        self.assertIn("CQ103", rules(code))

    def test_wrapper_pattern_is_clean(self):
        code = ("import functools\n\ndef _dec(fn):\n    @functools.wraps(fn)\n    def wrapper(*args, **kwargs):\n"
                "        return fn(*args, **kwargs)\n    return wrapper\n")
        self.assertEqual(analyze(code).findings, [])

    def test_decorated_class_is_clean(self):
        code = "from dataclasses import dataclass\n\n@dataclass\nclass _P:\n    x: int\n    y: int\n"
        self.assertEqual(analyze(code).findings, [])


if __name__ == "__main__":
    unittest.main()
