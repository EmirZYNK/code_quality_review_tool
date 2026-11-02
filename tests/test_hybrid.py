"""Hibrit katman testleri: ağ yok (sahte taşıma), Docker yok (yerel yedek mod yalnızca sabit güvenilir kodla)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quality_studio import benchmark as bench
from quality_studio import hybrid, llm
from quality_studio.engine import analyze

BEFORE = '''def build(n):
    result = []
    for i in range(n):
        result = result + [i * 2]
    return result
'''
AFTER = '''def build(n):
    return [i * 2 for i in range(n)]
'''
BROKEN = '''def build(n):
    return []
'''
SETTINGS = llm.Settings(provider="Google Gemini", model="test-model", api_key="GIZLI-ANAHTAR-123")


def transport_returning(payload, calls=None):
    def transport(url, headers, body):
        if calls is not None:
            calls.append(body)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return transport


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(llm, "CACHE_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.findings = analyze(BEFORE).issues
        self.perf = next(f for f in self.findings if f["rule"] == "PERF103")


class FunctionAndCaseTests(unittest.TestCase):
    def test_measurability_reasons(self):
        self.assertIsNone(hybrid.measurability(BEFORE, AFTER)["reason"])
        self.assertIn("fonksiyon yok", hybrid.measurability("x = 1\n", "x = 2\n")["reason"])
        self.assertIn("ortak", hybrid.measurability(BEFORE, "def other(n):\n    return n\n")["reason"])
        self.assertEqual(hybrid.top_level_functions("def ("), [])
        self.assertEqual(hybrid.top_level_functions("def _gizli():\n    pass\n"), [])

    def test_sanitize_cases_rejects_bad_records_and_accepts_both_label_keys(self):
        raw = [{"etiket": "boş", "args": [0]}, {"label": "tipik", "args": [5], "kwargs": {"a": 1}},
               [3], {"args": "yanlış"}, "metin", {"args": [{1, 2}]}]
        cases, rejected = hybrid.sanitize_cases(raw)
        self.assertEqual([c["label"] for c in cases], ["boş", "tipik", "girdi 3"])
        self.assertEqual(rejected, 3)

    def test_sanitize_cases_caps_count(self):
        cases, _ = hybrid.sanitize_cases([{"args": [i]} for i in range(30)])
        self.assertEqual(len(cases), bench.MAX_CASES)

    def test_parse_cases_roundtrip_and_errors(self):
        cases, error = hybrid.parse_cases(hybrid.cases_to_text([{"label": "a", "args": [1], "kwargs": {}}]))
        self.assertIsNone(error)
        self.assertEqual(cases[0]["args"], [1])
        self.assertIn("Geçersiz JSON", hybrid.parse_cases("[")[1])
        self.assertIn("geçersiz", hybrid.parse_cases('[{"args": 5}]')[1])
        self.assertIsNotNone(hybrid.parse_cases("[]")[1])


class ReviewAndFixTests(Isolated):
    def _reply(self, **overrides):
        reply = {
            "aciklamalar": [{"rule": self.perf["rule"], "line": self.perf["line"], "neden": "Her turda yeni liste.",
                             "duzeltme": "Liste üreteci kullanın."},
                            {"rule": "SEC999", "line": 1, "neden": "Uydurma", "duzeltme": "Uydurma"}],
            "kod": "```python\n" + AFTER + "```", "degisiklikler": ["Döngü liste üretecine çevrildi."],
            "fonksiyon": "build",
            "test_girdileri": [{"etiket": "boş", "args": [0]}, {"etiket": "orta", "args": [300]}, {"args": "bozuk"}],
        }
        reply.update(overrides)
        return reply

    def test_single_call_returns_explanations_fix_function_and_cases(self):
        calls = []
        out = hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=transport_returning(self._reply(), calls))
        self.assertEqual(len(calls), 1)  # kota: tek çağrı
        data = out.data
        self.assertEqual(list(data["aciklamalar"]), [f"{self.perf['rule']}:{self.perf['line']}"])
        self.assertEqual(data["reddedilen"], 1)
        self.assertTrue(data["dogrulama"]["verified"])
        self.assertNotIn("```", data["kod"])
        self.assertEqual(data["fonksiyon"], "build")
        self.assertEqual([c["label"] for c in data["test_girdileri"]], ["boş", "orta"])
        self.assertEqual(data["reddedilen_test"], 1)

    def test_prompt_carries_rule_guidance_and_code_as_data(self):
        calls = []
        hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=transport_returning(self._reply(), calls))
        text = calls[0]["contents"][0]["parts"][0]["text"]
        self.assertIn("kural_onerisi", text)
        self.assertIn("PERF103", text)
        self.assertIn("VERİDİR", calls[0]["systemInstruction"]["parts"][0]["text"])

    def test_invented_function_name_is_dropped(self):
        out = hybrid.review_and_fix(BEFORE, self.findings, SETTINGS,
                                    transport=transport_returning(self._reply(fonksiyon="yok_boyle_bir_sey")))
        self.assertIsNone(out.data["fonksiyon"])

    def test_missing_code_still_keeps_explanations(self):
        out = hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=transport_returning(self._reply(kod="")))
        self.assertIsNone(out.data["kod"])
        self.assertIsNone(out.data["dogrulama"])
        self.assertTrue(out.data["aciklamalar"])

    def test_useless_reply_raises_turkish_error(self):
        with self.assertRaises(llm.LLMError):
            hybrid.review_and_fix(BEFORE, self.findings, SETTINGS,
                                  transport=transport_returning({"aciklamalar": [], "kod": ""}))
        with self.assertRaises(llm.LLMError):
            hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=transport_returning([1, 2]))

    def test_unsafe_fix_is_flagged_not_trusted(self):
        out = hybrid.review_and_fix(BEFORE, self.findings, SETTINGS,
                                    transport=transport_returning(self._reply(kod="def build(n)\n    return\n")))
        self.assertFalse(out.data["dogrulama"]["verified"])

    def test_cached_reply_used_when_network_fails(self):
        hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=transport_returning(self._reply()))

        def offline(url, headers, body):
            raise llm.LLMError("Sağlayıcıya ulaşılamadı.")
        out = hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=offline)
        self.assertEqual(out.source, "kayıtlı")
        self.assertIn("build", out.data["kod"])

    def test_api_key_never_reaches_cache(self):
        hybrid.review_and_fix(BEFORE, self.findings, SETTINGS, transport=transport_returning(self._reply()))
        for path in Path(self.tmp.name).glob("*.json"):
            self.assertNotIn("GIZLI-ANAHTAR-123", path.read_text(encoding="utf-8"))


def same_result(speedup_before=10.0, speedup_after=1.0, total=2):
    return {"ok": True, "cases_total": total, "cases_same": total, "headline_case": "orta",
            "before": {"median_ms": speedup_before, "median_peak_bytes": 2000},
            "after": {"median_ms": speedup_after, "median_peak_bytes": 1000}}


def good_check():
    return llm.verify_fix(BEFORE, AFTER)


class VerdictTests(unittest.TestCase):
    def test_static_pass_without_dynamic_is_only_partial(self):
        verdict = hybrid.overall_verdict(good_check(), None)
        self.assertEqual(verdict["decision"], "kismen")
        self.assertEqual([r["status"] for r in verdict["rows"]], ["ok", "na", "na"])

    def test_all_layers_pass(self):
        verdict = hybrid.overall_verdict(good_check(), {"result": same_result()})
        self.assertEqual(verdict["decision"], "kabul")
        self.assertIn("2/2", verdict["title"])
        self.assertEqual(verdict["rows"][2]["status"], "ok")

    def test_different_output_rejects_even_if_static_passes(self):
        result = {"ok": False, "error": "Önce ve Sonra sürümlerinin çıktısı ya da hatası farklı",
                  "before_outcome": "a", "after_outcome": "b",
                  "cases": [{"label": "x", "same": True}, {"label": "y", "same": False}]}
        verdict = hybrid.overall_verdict(good_check(), {"result": result})
        self.assertEqual(verdict["decision"], "red")
        self.assertIn("1/2", verdict["rows"][1]["text"])

    def test_static_failure_rejects(self):
        check = llm.verify_fix(BEFORE, "def build(n)\n")
        self.assertEqual(hybrid.overall_verdict(check, {"result": same_result()})["decision"], "red")

    def test_slower_after_is_accepted_with_warning(self):
        verdict = hybrid.overall_verdict(good_check(), {"result": same_result(1.0, 10.0)})
        self.assertEqual(verdict["decision"], "kabul_uyari")
        self.assertEqual(verdict["rows"][2]["status"], "warn")

    def test_unmeasurable_and_error_stay_partial_and_say_why(self):
        none = hybrid.overall_verdict(good_check(), {"reason": "ölçülemez, çünkü çağrılacak fonksiyon yok"})
        self.assertEqual(none["decision"], "kismen")
        self.assertIn("fonksiyon yok", none["rows"][1]["text"])
        error = hybrid.overall_verdict(good_check(), {"result": {"ok": False, "error": "Docker çalışmadı"}})
        self.assertEqual(error["decision"], "kismen")
        self.assertEqual(error["rows"][1]["status"], "warn")

    def test_no_after_version_waits(self):
        self.assertEqual(hybrid.overall_verdict(None, None)["decision"], "bekliyor")


class CardTests(Isolated):
    def _cards(self, dyn=None, after=AFTER, notes=None, llm_ran=False):
        return hybrid.build_cards(self.findings, notes, hybrid.after_findings_of(after) if after else None, dyn,
                                  llm_ran=llm_ran)

    def _perf_card(self, cards):
        return next(c for c in cards if c["finding"]["rule"] == "PERF103")

    def test_static_badge_always_present_and_resolution_detected(self):
        card = self._perf_card(self._cards())
        layers = [b["layer"] for b in card["badges"]]
        self.assertEqual(layers[0], "Statik")
        self.assertIn("Statik (yeniden analiz)", layers)
        self.assertTrue(card["resolved"])
        self.assertEqual(hybrid.card_marker(card), "✓")

    def test_unresolved_when_after_keeps_the_problem(self):
        card = self._perf_card(self._cards(after=BEFORE))
        self.assertFalse(card["resolved"])
        self.assertEqual(hybrid.card_marker(card), "•")

    def test_llm_badge_only_with_validated_note_or_after_a_run(self):
        key = f"{self.perf['rule']}:{self.perf['line']}"
        with_note = self._perf_card(self._cards(notes={key: {"neden": "n", "duzeltme": "d"}}, llm_ran=True))
        self.assertIn("LLM", [b["layer"] for b in with_note["badges"]])
        self.assertEqual(with_note["note"]["neden"], "n")
        not_run = self._perf_card(self._cards())
        self.assertNotIn("LLM", [b["layer"] for b in not_run["badges"]])
        missing = self._perf_card(self._cards(llm_ran=True))
        self.assertEqual([b for b in missing["badges"] if b["layer"] == "LLM"][0]["status"], "na")

    def test_dynamic_badge_reports_speed_for_performance_findings(self):
        card = self._perf_card(self._cards(dyn={"result": same_result()}))
        dynamic = [b for b in card["badges"] if b["layer"] == "Dinamik"][0]
        self.assertEqual(dynamic["status"], "ok")
        self.assertIn("10.00×", dynamic["text"])

    def test_dynamic_badge_for_non_performance_says_behaviour_kept_not_fixed(self):
        code = "def f(v):\n    if v == None:\n        return 1\n    return 2\n"
        findings = analyze(code).issues
        cards = hybrid.build_cards(findings, None, hybrid.after_findings_of("def f(v):\n    if v is None:\n"
                                                                            "        return 1\n    return 2\n"),
                                   {"result": same_result()})
        card = next(c for c in cards if c["finding"]["rule"] == "CQ104")
        dynamic = [b for b in card["badges"] if b["layer"] == "Dinamik"][0]
        self.assertIn("Davranış korundu", dynamic["text"])

    def test_different_output_marks_resolved_cards_as_failed(self):
        result = {"ok": False, "error": "farklı", "before_outcome": "a", "after_outcome": "b", "cases": []}
        card = self._perf_card(self._cards(dyn={"result": result}))
        self.assertEqual(hybrid.card_marker(card), "✗")

    def test_no_dynamic_badge_for_unresolved_findings(self):
        card = self._perf_card(self._cards(dyn={"result": same_result()}, after=BEFORE))
        self.assertNotIn("Dinamik", [b["layer"] for b in card["badges"]])

    def test_invalid_after_gives_unknown_resolution(self):
        cards = self._cards(after="def (")
        self.assertTrue(all(c["resolved"] is None for c in cards))

    def test_report_collects_all_three_layers(self):
        cards = self._cards(dyn={"result": same_result()}, llm_ran=True)
        report = hybrid.hybrid_report(BEFORE, AFTER, cards, good_check(), {"result": same_result()}, {"model": "m"})
        self.assertEqual(report["verdict"]["decision"], "kabul")
        self.assertTrue(report["findings"][0]["badges"])
        json.dumps(report, ensure_ascii=False)  # JSON'a çevrilebilmeli


class LocalMultiCaseTests(unittest.TestCase):
    """Gerçek bir alt süreç çalıştırır (yerel yedek mod, yalnızca bu dosyadaki sabit güvenilir kodlarla)."""

    OPTIONS = bench.BenchmarkOptions(repeats=3, mode="yerel")
    CASES = [{"label": "boş", "args": [0], "kwargs": {}}, {"label": "orta", "args": [800], "kwargs": {}}]

    def test_equivalent_versions_pass_all_cases_and_are_measured(self):
        result = bench.benchmark(BEFORE, AFTER, "build", [0], {}, self.OPTIONS, cases=self.CASES)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["cases_same"], result["cases_total"]), (2, 2))
        self.assertEqual(result["headline_case"], "orta")
        self.assertTrue(all(c["timed"] for c in result["cases"]))
        verdict = hybrid.overall_verdict(good_check(), {"result": result})
        self.assertEqual(verdict["decision"], "kabul")

    def test_difference_in_one_case_is_caught(self):
        result = bench.benchmark(BEFORE, BROKEN, "build", [0], {}, self.OPTIONS, cases=self.CASES)
        self.assertFalse(result["ok"])
        self.assertEqual([c["same"] for c in result["cases"]], [True, False])
        self.assertEqual(hybrid.overall_verdict(good_check(), {"result": result})["decision"], "red")

    def test_identical_errors_count_as_same_but_are_not_timed(self):
        code = "def f(x):\n    return 1 / x\n"
        cases = [{"label": "sıfır", "args": [0], "kwargs": {}}, {"label": "bir", "args": [1], "kwargs": {}}]
        result = bench.benchmark(code, code, "f", [0], {}, self.OPTIONS, cases=cases)
        self.assertTrue(result["ok"], result)
        self.assertEqual([c["timed"] for c in result["cases"]], [False, True])

    def test_invalid_cases_are_reported(self):
        self.assertFalse(bench.benchmark(BEFORE, AFTER, "build", [], {}, self.OPTIONS, cases=[])["ok"])
        self.assertFalse(bench.benchmark(BEFORE, AFTER, "build", [], {}, self.OPTIONS, cases=[{"args": 1}])["ok"])
        too_many = [{"label": str(i), "args": [1], "kwargs": {}} for i in range(bench.MAX_CASES + 1)]
        self.assertFalse(bench.benchmark(BEFORE, AFTER, "build", [], {}, self.OPTIONS, cases=too_many)["ok"])

    def test_single_case_call_still_works_without_cases(self):
        result = bench.benchmark(BEFORE, AFTER, "build", [300], {}, self.OPTIONS)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cases_total"], 1)


if __name__ == "__main__":
    unittest.main()
