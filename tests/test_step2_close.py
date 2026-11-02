"""Adım 2'nin kapanışı: LLM kararı kod üstünde, daha sıkı eleme gerekçesi, ayrı 'gönderilmedi' sayacı."""
import unittest

from quality_studio import hybrid
from quality_studio.codeview import render_code
from quality_studio.engine import analyze

CODE = 'def f(v):\n    return eval(v)\n'


def _finding(rule="SEC101", line=2, severity="high", category="Security"):
    return {"rule": rule, "line": line, "end_line": line, "severity": severity, "category": category,
            "message": "m", "why": "w", "fix": "f", "confidence": "high"}


def _records(verdict, reason, severity="high"):
    f = _finding(severity=severity)
    valid = {(f["rule"], f["line"])}
    return hybrid._clean_explanations([{"rule": f["rule"], "line": f["line"], "karar": verdict, "gerekce": reason,
                                        "neden": "n", "duzeltme": "d"}], valid, {(f["rule"], f["line"]): severity})


class EliminationReasonTests(unittest.TestCase):
    def test_short_elimination_reason_is_dropped(self):
        notes, _rejected, dropped = _records("yanlis_pozitif", "Sorun yok.", severity="low")
        self.assertIsNone(notes["SEC101:2"]["karar"])
        self.assertEqual(dropped, 1)

    def test_normal_elimination_reason_is_kept_for_low_severity(self):
        notes, _r, dropped = _records("yanlis_pozitif", "eval yalnızca sabit bir metne uygulanıyor.", severity="low")
        self.assertEqual(notes["SEC101:2"]["karar"], "yanlis_pozitif")
        self.assertEqual(dropped, 0)

    def test_high_severity_needs_a_longer_reason(self):
        notes, _r, dropped = _records("yanlis_pozitif", "eval yalnızca sabit bir metne uygulanıyor.")
        self.assertIsNone(notes["SEC101:2"]["karar"])  # 25+ ama 60'tan kısa
        self.assertEqual(dropped, 1)
        long_reason = "eval yalnızca satır 5'teki sabit metne uygulanıyor, kullanıcı girdisi bu değişkene hiç ulaşmıyor."
        notes, _r, dropped = _records("yanlis_pozitif", long_reason)
        self.assertEqual(notes["SEC101:2"]["karar"], "yanlis_pozitif")
        self.assertEqual(dropped, 0)

    def test_dropped_elimination_keeps_the_finding_active(self):
        notes, *_ = _records("yanlis_pozitif", "Sorun yok.")
        cards = hybrid.build_cards([_finding()], notes, None, None, llm_ran=True)
        self.assertEqual(len(hybrid.active_findings(cards)), 1)


class CodeAnnotationTests(unittest.TestCase):
    def cards(self, verdict):
        notes = {"SEC101:2": {"neden": "Tehlikeli.", "duzeltme": "ast.literal_eval kullan.", "karar": verdict,
                              "gerekce": "Bu kodda girdi dışarıdan geliyor."}}
        return hybrid.build_cards([_finding()], notes, None, None, llm_ran=True)

    def test_each_verdict_becomes_a_note_on_its_line(self):
        for verdict, kind in (("onayla", "onay"), ("yanlis_pozitif", "eledi"), ("baglama_bagli", "baglam"), (None, "aciklama")):
            notes = hybrid.code_annotations(self.cards(verdict))
            self.assertEqual([(n["line"], n["kind"]) for n in notes], [(2, kind)])

    def test_extra_issues_are_marked_unverified(self):
        extra = {"line": 1, "title": "Yanıltıcı ad", "why": "x", "fix": "y", "status": "unverified"}
        notes = hybrid.code_annotations([], [extra])
        self.assertEqual(notes[0]["kind"], "ek")
        self.assertIn("doğrulanmadı", notes[0]["label"])

    def test_render_shows_notes_dims_eliminated_lines_and_escapes(self):
        notes = hybrid.code_annotations(self.cards("yanlis_pozitif"))
        html = render_code(CODE, analyze(CODE).issues, annotations=notes + [
            {"line": 1, "kind": "ek", "label": "<b>x</b>", "text": '<script>alert(1)</script>'}])
        self.assertIn('class="line dim"', html)
        self.assertIn('class="note eledi"', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("LLM notu", html)

    def test_render_without_annotations_is_unchanged(self):
        html = render_code(CODE, analyze(CODE).issues)
        self.assertNotIn('class="note', html)
        self.assertNotIn(" dim", html)


class UnsentCounterTests(unittest.TestCase):
    def test_ai_pattern_findings_are_counted_as_not_sent(self):
        findings = [_finding(), _finding("AIP001", 1, "info", "AI Pattern")]
        notes = {"SEC101:2": {"neden": "n", "duzeltme": "d", "karar": "onayla", "gerekce": ""}}
        summary = hybrid.verdict_summary(hybrid.build_cards(findings, notes, None, None, llm_ran=True), 2)
        self.assertEqual((summary["gonderilmedi"], summary["karar_yok"]), (1, 0))

    def test_finding_without_answer_is_karar_yok(self):
        summary = hybrid.verdict_summary(hybrid.build_cards([_finding()], {}, None, None, llm_ran=True), 2)
        self.assertEqual((summary["gonderilmedi"], summary["karar_yok"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
