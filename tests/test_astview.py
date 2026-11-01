"""Statik analiz görünümü (AST ağacı ve bulgu-düğüm eşlemesi) testleri."""
import unittest

from quality_studio import astview
from quality_studio.engine import analyze

CODE = (
    "def q(u, db):\n"
    "    db.execute('SELECT * FROM t WHERE id=' + u)\n"
)


class AstViewTests(unittest.TestCase):
    def setUp(self):
        self.findings = analyze(CODE).issues

    def test_tree_marks_flagged_node(self):
        text = astview.tree_text(CODE, self.findings)
        self.assertIn("FunctionDef 'q'", text)
        self.assertIn("SEC104", text)

    def test_only_flagged_hides_unrelated_nodes(self):
        full = astview.tree_text(CODE, self.findings)
        small = astview.tree_text(CODE, self.findings, only_flagged=True)
        self.assertLess(len(small), len(full))
        self.assertIn("SEC104", small)

    def test_rules_not_repeated_on_one_node(self):
        code = "import os, sys, json\n"
        text = astview.tree_text(code, analyze(code).issues)
        self.assertEqual(text.count("RED001"), 1)

    def test_finding_nodes_report_node_types(self):
        rows = astview.finding_nodes(CODE, self.findings)
        sec = next(r for r in rows if r["Kural"] == "SEC104")
        self.assertIn("Call", sec["AST düğümü"])
        self.assertEqual(sec["Kategori"], "Güvenlik")

    def test_rule_table_covers_catalog_and_counts_hits(self):
        rows = astview.rule_table(self.findings)
        self.assertEqual(len(rows), 36)
        self.assertEqual(next(r for r in rows if r["Kural"] == "SEC104")["Bu kodda"], 1)

    def test_category_counts_use_turkish_names(self):
        self.assertIn("Güvenlik", astview.category_counts(self.findings))

    def test_depth_limit(self):
        code = "x = 1\n" * 50
        text = astview.tree_text(code, [], max_rows=10)
        self.assertIn("ilk 10 düğüm", text)


if __name__ == "__main__":
    unittest.main()
