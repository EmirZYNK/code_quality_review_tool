import unittest

from quality_studio.compare import (bootstrap_mean_difference, cliffs_delta, compare_metric,
                                    delta_magnitude, evaluate_dataset, load_dataset)
from quality_studio.evaluation import evaluate, wilson_interval
from quality_studio.ground_truth import CASES

GOOD = "def _a(x):\n    return x\n"
BAD = "def _b(x):\n    return eval(x)\n"


class DatasetLoadingTests(unittest.TestCase):
    def test_missing_columns_gives_clear_error(self):
        with self.assertRaisesRegex(ValueError, "label.*code"):
            load_dataset("name,source\nx,y\n")

    def test_empty_and_row_limit(self):
        with self.assertRaises(ValueError):
            load_dataset("label,code\n")
        with self.assertRaisesRegex(ValueError, "En fazla 2 örnek"):
            load_dataset("label,code\na,x\na,y\na,z\n", max_rows=2)

    def test_large_field_not_limited_to_128kb(self):
        big = "x = 1\n" * 30_000  # ~180 KB, above csv's default 131072-char field limit
        samples = load_dataset(f'label,code\nhuman,"{big}"\n')
        self.assertEqual(len(samples[0][1]), len(big))

    def test_oversize_sample_has_row_number(self):
        with self.assertRaisesRegex(ValueError, "Satır 2"):
            load_dataset("label,code\nh,aaaaaaaaaa\n", max_chars=5)

    def test_bytes_with_bom(self):
        self.assertEqual(load_dataset("\ufefflabel,code\na,x\n".encode("utf-8"))[0][0], "a")


class StatisticsTests(unittest.TestCase):
    def test_cliffs_delta_extremes(self):
        self.assertEqual(cliffs_delta([5, 6, 7], [1, 2, 3]), 1.0)
        self.assertEqual(cliffs_delta([1, 2, 3], [5, 6, 7]), -1.0)
        self.assertEqual(cliffs_delta([1, 2], [1, 2]), 0.0)

    def test_delta_magnitude_labels(self):
        self.assertEqual(delta_magnitude(0.05), "negligible")
        self.assertEqual(delta_magnitude(-0.2), "small")
        self.assertEqual(delta_magnitude(0.4), "medium")
        self.assertEqual(delta_magnitude(0.9), "large")

    def test_bootstrap_is_reproducible_and_brackets_difference(self):
        a, b = [10, 12, 14, 16], [1, 2, 3, 4]
        low, high = bootstrap_mean_difference(a, b, seed=1)
        self.assertEqual((low, high), bootstrap_mean_difference(a, b, seed=1))
        self.assertLess(low, 10.0)
        self.assertGreater(high, 10.0)

    def test_identical_values_give_p_one(self):
        stat = compare_metric([0.0] * 5, [0.0] * 5)
        self.assertEqual(stat["p_value"], 1.0)

    def test_needs_two_samples_per_group(self):
        self.assertFalse(compare_metric([1.0], [2.0, 3.0])["computable"])


class DatasetEvaluationTests(unittest.TestCase):
    def test_separates_clean_and_vulnerable_groups(self):
        samples = [("human", GOOD)] * 12 + [("ai", BAD)] * 12
        result = evaluate_dataset(samples)
        stat = result["comparisons"][0]["defect_density"]
        self.assertLess(stat["p_value"], 0.01)
        self.assertEqual(stat["effect_size"], "large")

    def test_syntax_errors_are_counted_not_dropped_silently(self):
        samples = [("human", GOOD)] * 5 + [("ai", BAD)] * 5 + [("ai", "def (:")] * 5
        result = evaluate_dataset(samples)
        ai = result["labels"]["ai"]
        self.assertEqual((ai["samples"], ai["valid_samples"], ai["syntax_errors"]), (10, 5, 5))
        self.assertEqual(ai["syntax_error_rate"], 0.5)
        self.assertTrue(any("sözdizimi hatası" in w for w in result["warnings"]))
        self.assertAlmostEqual(result["comparisons"][0]["syntax_error_fisher_p"], 0.1009, places=3)

    def test_small_groups_are_warned(self):
        result = evaluate_dataset([("a", GOOD), ("b", BAD)])
        self.assertTrue(any("güvenilmezdir" in w for w in result["warnings"]))

    def test_multiple_labels_bonferroni(self):
        samples = [(label, code) for label, code in (("a", GOOD), ("b", BAD), ("c", GOOD)) for _ in range(6)]
        result = evaluate_dataset(samples)
        self.assertEqual(len(result["comparisons"]), 3)
        for pair in result["comparisons"]:
            stat = pair["defect_density"]
            self.assertGreaterEqual(stat["p_adjusted_bonferroni"], stat["p_value"])


class GroundTruthTests(unittest.TestCase):
    def test_case_ids_unique_and_labels_known(self):
        from quality_studio.rules import RULES
        ids = [c.id for c in CASES]
        self.assertEqual(len(ids), len(set(ids)))
        for case in CASES:
            self.assertLessEqual(case.expected, set(RULES), case.id)

    def test_every_rule_has_positive_and_negative_coverage(self):
        from quality_studio.rules import RULES
        positives = {r for c in CASES for r in c.expected}
        self.assertEqual(set(RULES) - positives, set())

    def test_only_documented_limitations_disagree(self):
        report = evaluate()
        for mismatch in report["mismatches"]:
            self.assertIn("bilinen sınırlama", mismatch["note"], mismatch)
            self.assertEqual(mismatch["unexpected"], [])

    def test_quality_floor(self):
        micro = evaluate()["micro"]
        self.assertGreaterEqual(micro["precision"], 0.95)
        self.assertGreaterEqual(micro["recall"], 0.9)

    def test_wilson_interval(self):
        low, high = wilson_interval(50, 50)
        self.assertGreater(low, 0.9)
        self.assertEqual(high, 1.0)
        self.assertIsNone(wilson_interval(0, 0))


if __name__ == "__main__":
    unittest.main()
