"""Adım 3-4: sıfır tolerans, çok fonksiyonlu ve tam davranış karşılaştırmalı dinamik doğrulama."""
import unittest

from quality_studio import benchmark as bench
from quality_studio import hybrid, llm
from quality_studio.engine import analyze

LOCAL = bench.BenchmarkOptions(repeats=3, mode="yerel")


def run_local(before, after, function, cases):
    return bench.benchmark(before, after, function, cases[0]["args"], cases[0]["kwargs"], LOCAL, cases=cases)


def case(*args, function=None, label="g"):
    return {"label": label, "args": list(args), "kwargs": {}, **({"function": function} if function else {})}


class ZeroToleranceTests(unittest.TestCase):
    BEFORE = 'def f(l):\n    """Listeyi kopyalar."""\n    r = []\n    for x in l:\n        r = r + [x]\n    return r\n'

    def test_low_severity_new_finding_rejects_fix(self):
        after = ('def f(l):\n    """Listeyi kopyalar."""\n    return list(l)\n\n\n'
                 'def yardimci(x):\n    return x\n')   # docstring'siz yeni genel fonksiyon -> CQ103 (düşük önem)
        check = llm.verify_fix(self.BEFORE, after)
        self.assertFalse(check["verified"])
        failed = [c for c in check["checks"] if not c["gecti"]]
        self.assertEqual([c["kontrol"] for c in failed], ["Yeni bulgu eklenmedi"])
        self.assertIn("CQ103", failed[0]["ayrinti"])

    def test_clean_fix_passes(self):
        after = 'def f(l):\n    """Listeyi kopyalar."""\n    return list(l)\n'
        self.assertTrue(llm.verify_fix(self.BEFORE, after)["verified"])


class RunnerBehaviorTests(unittest.TestCase):
    def test_equal_generators_are_not_rejected(self):
        before = "def g(n):\n    for i in range(n):\n        yield i\n"
        after = "def g(n):\n    yield from range(n)\n"
        self.assertTrue(run_local(before, after, "g", [case(5)])["ok"])

    def test_different_generators_are_rejected(self):
        before = "def g(n):\n    for i in range(n):\n        yield i\n"
        after = "def g(n):\n    yield from range(n + 1)\n"
        result = run_local(before, after, "g", [case(3)])
        self.assertFalse(result["ok"])
        self.assertIn("dönüş değeri", result["error"])

    def test_in_place_mutation_is_detected(self):
        result = run_local("def s(l):\n    l.sort()\n    return l\n", "def s(l):\n    return sorted(l)\n", "s", [case([3, 1, 2])])
        self.assertFalse(result["ok"])
        self.assertIn("yerinde", result["error"])

    def test_lost_print_is_detected(self):
        result = run_local("def p(x):\n    print(x)\n    return x\n", "def p(x):\n    return x\n", "p", [case(1)])
        self.assertFalse(result["ok"])
        self.assertIn("print", result["error"])

    def test_module_level_output_difference_is_detected(self):
        result = run_local("print('a')\ndef f(x):\n    return x\n", "def f(x):\n    return x\n", "f", [case(1)])
        self.assertFalse(result["ok"])
        self.assertTrue(any(r["label"] == "(modül yüklenirken)" for r in result["cases"]))

    def test_nan_results_count_as_equal(self):
        src = "def n(x):\n    return float('nan')\n"
        self.assertTrue(run_local(src, src, "n", [case(1)])["ok"])

    def test_cases_can_target_different_functions(self):
        before = "def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2\n"
        after = "def a(x):\n    return 1 + x\n\n\ndef b(x):\n    return x + x\n"
        result = run_local(before, after, "a", [case(2, function="a", label="a1"), case(3, function="b", label="b1")])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["functions_tested"], ["a", "b"])
        self.assertEqual(sorted(result["by_function"]), ["a", "b"])

    def test_wrong_behavior_in_second_function_is_caught(self):
        before = "def a(x):\n    return x\n\n\ndef b(x):\n    return x * 2\n"
        after = "def a(x):\n    return x\n\n\ndef b(x):\n    return x * 3\n"
        result = run_local(before, after, "a", [case(2, function="a"), case(3, function="b")])
        self.assertFalse(result["ok"])
        self.assertEqual([r["function"] for r in result["cases"] if not r["same"]], ["b"])

    def test_missing_function_is_reported(self):
        result = run_local("def a():\n    return 1\n", "def a():\n    return 1\n", "a", [case(function="yok")])
        self.assertFalse(result["ok"])
        self.assertIn("yok", result["error"])

    def test_private_function_name_in_case_is_rejected(self):
        self.assertIsNotNone(bench._validate_cases([{"label": "x", "args": [], "kwargs": {}, "function": "_gizli"}]))


class CoverageTests(unittest.TestCase):
    BEFORE = "def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x\n\n\nclass C:\n    pass\n"
    AFTER = "def a(x):\n    # yorum\n    return 1 + x\n\n\ndef b(x):\n    return x\n\n\nclass C:\n    y = 1\n"

    def test_only_really_changed_units_are_listed(self):
        changed = hybrid.changed_units(self.BEFORE, self.AFTER)
        self.assertEqual(changed["public"], ["a"])
        self.assertEqual(changed["indirect"], ["C"])

    def test_comment_only_change_is_not_a_change(self):
        self.assertEqual(hybrid.changed_units("def a():\n    return 1\n", "def a():\n    # not\n    return 1\n")["public"], [])

    def test_untested_changed_function_blocks_acceptance(self):
        coverage = hybrid.test_coverage(self.BEFORE, self.AFTER, [{"function": "b"}])
        self.assertEqual(coverage["untested"], ["a"])
        self.assertFalse(coverage["complete"])
        self.assertIn("a", hybrid.coverage_text(coverage))

    def test_default_function_counts_for_cases_without_name(self):
        self.assertTrue(hybrid.test_coverage(self.BEFORE, self.AFTER, [{}], "a")["complete"])

    def test_parse_cases_keeps_function(self):
        cases, error = hybrid.parse_cases('[{"etiket": "x", "fonksiyon": "a", "args": [1], "kwargs": {}}]')
        self.assertIsNone(error)
        self.assertEqual(cases[0]["function"], "a")
        self.assertIn("fonksiyon", hybrid.cases_to_text(cases))
        self.assertIsNotNone(hybrid.parse_cases('[{"fonksiyon": "_x", "args": [], "kwargs": {}}]')[1])


def _check(verified=True, introduced=0):
    return {"verified": verified, "checks": [{"kontrol": "k", "gecti": verified, "ayrinti": ""}],
            "effect": {"resolved": 1, "introduced": introduced, "resolved_rules": [], "introduced_rules": []}}


def _dyn(functions, same=True):
    """functions: ad -> (önce_ms, sonra_ms)."""
    by_function, rows = {}, []
    for name, (before, after) in functions.items():
        side = lambda ms: {"median_ms": ms, "median_peak_bytes": 1000}  # noqa: E731
        by_function[name] = {"case": "orta", "before": side(before), "after": side(after), "cases": 1}
        rows.append({"label": "orta", "function": name, "same": same, "diff": [] if same else ["dönüş değeri ya da hata"],
                     "before_outcome": "a", "after_outcome": "b"})
    slowest = max(by_function.values(), key=lambda d: d["before"]["median_ms"])
    result = {"ok": same, "cases_total": len(rows), "cases_same": len(rows), "cases": rows, "headline_case": "orta",
              "by_function": by_function, "before": slowest["before"], "after": slowest["after"]}
    if not same:
        result.update(error="farklı", before_outcome="a", after_outcome="b")
    return {"result": result, "sources": ("", "", "a"), "mode": "yerel"}


BEFORE = "def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2\n"
AFTER = "def a(x):\n    return 1 + x\n\n\ndef b(x):\n    return x + x\n"


class VerdictTests(unittest.TestCase):
    def verdict(self, dyn, perf=(), allow=False):
        return hybrid.overall_verdict(_check(), dyn, allow, sources=(BEFORE, AFTER), perf_functions=perf)

    def test_full_coverage_and_speedup_is_accepted(self):
        result = self.verdict(_dyn({"a": (5.0, 1.0), "b": (4.0, 1.0)}), perf=("a", "b"))
        self.assertEqual(result["decision"], "kabul", result["title"])

    def test_untested_function_gives_partial_not_accept(self):
        result = self.verdict(_dyn({"a": (5.0, 1.0)}))
        self.assertEqual(result["decision"], "kismen")
        self.assertIn("b", result["title"])

    def test_speedup_not_proven_by_measurement_is_warned(self):
        result = self.verdict(_dyn({"a": (5.0, 1.0), "b": (4.0, 4.1)}), perf=("a", "b"))
        self.assertEqual(result["decision"], "kabul_uyari")
        self.assertEqual(result["unproven"], ["b"])
        self.assertTrue(any(r["layer"] == "Dinamik: hız kanıtı" for r in result["rows"]))

    def test_one_slower_function_is_not_hidden_by_overall_speedup(self):
        result = self.verdict(_dyn({"a": (50.0, 1.0), "b": (2.0, 3.0)}))
        self.assertEqual(result["decision"], "kabul_uyari")
        self.assertIn("yavaş", result["title"])
        self.assertIn("b", result["title"])

    def test_tiny_time_differences_are_noise(self):
        state = hybrid.dynamic_state(_dyn({"a": (0.010, 0.014), "b": (0.010, 0.010)}))
        self.assertEqual(state["slower_functions"], [])

    def test_behavior_difference_is_still_rejected(self):
        self.assertEqual(self.verdict(_dyn({"a": (1.0, 1.0), "b": (1.0, 1.0)}, same=False))["decision"], "red")

    def test_refinement_feedback_names_the_difference_and_unproven_speedup(self):
        diff = hybrid.rejection_feedback(BEFORE, AFTER, _check(), _dyn({"a": (1.0, 1.0)}, same=False))
        self.assertTrue(any("yerinde" in note and "print" in note for note in diff))
        notes = hybrid.rejection_feedback(BEFORE, AFTER, _check(), _dyn({"a": (5.0, 1.0), "b": (4.0, 4.0)}), ("b",))
        self.assertTrue(any("comprehension" in note for note in notes))


class PerformanceFunctionTests(unittest.TestCase):
    def test_functions_with_performance_findings(self):
        code = ("def yavas(a):\n    r = []\n    for i in range(len(a)):\n        r = r + [i]\n    return r\n\n\n"
                "def hizli(a):\n    return list(a)\n")
        names = hybrid.performance_functions(code, analyze(code).issues)
        self.assertEqual(names, ["yavas"])


if __name__ == "__main__":
    unittest.main()
