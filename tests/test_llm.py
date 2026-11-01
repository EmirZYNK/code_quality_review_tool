"""LLM katmanı testleri: ağ yok, sahte taşıma katmanıyla. Doğrulama zincirini ve güvenlik kurallarını sınar."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quality_studio import llm
from quality_studio.engine import analyze

CODE = '''def build(n):
    result = []
    for i in range(n):
        result = result + [i * 2]
    return result


def find(uid):
    query = "SELECT * FROM t WHERE id = " + uid
    return db.execute(query)
'''
SETTINGS = llm.Settings(provider="Google Gemini", model="test-model", api_key="GIZLI-ANAHTAR-123")


def gemini_reply(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def transport_returning(payload, calls=None):
    def transport(url, headers, body):
        if calls is not None:
            calls.append((url, headers, body))
        return gemini_reply(payload)
    return transport


class CacheIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(llm, "CACHE_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.findings = analyze(CODE).issues


class ParseTests(unittest.TestCase):
    def test_fenced_and_wrapped_json(self):
        self.assertEqual(llm.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json('İşte yanıt: {"a": 1} bitti'), {"a": 1})

    def test_garbage_raises_turkish_error(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json("json değil")


class ExplainTests(CacheIsolation):
    def test_valid_records_kept_and_invented_ones_rejected(self):
        real = self.findings[0]
        reply = {"aciklamalar": [
            {"rule": real["rule"], "line": real["line"], "neden": "Neden.", "duzeltme": "Düzelt."},
            {"rule": "SEC999", "line": 1, "neden": "Uydurma", "duzeltme": "Uydurma"},
        ]}
        out = llm.explain_findings(CODE, self.findings, SETTINGS, transport=transport_returning(reply))
        self.assertEqual(list(out.data["aciklamalar"]), [f"{real['rule']}:{real['line']}"])
        self.assertEqual(out.data["reddedilen"], 1)
        self.assertEqual(out.source, "canlı")

    def test_api_key_is_header_only_never_in_url_body_or_cache(self):
        calls = []
        real = self.findings[0]
        reply = {"aciklamalar": [{"rule": real["rule"], "line": real["line"], "neden": "a", "duzeltme": "b"}]}
        llm.explain_findings(CODE, self.findings, SETTINGS, transport=transport_returning(reply, calls))
        url, headers, body = calls[0]
        self.assertNotIn("GIZLI-ANAHTAR", url)
        self.assertNotIn("GIZLI-ANAHTAR", json.dumps(body))
        self.assertEqual(headers["x-goog-api-key"], "GIZLI-ANAHTAR-123")
        for path in Path(self.tmp.name).glob("*.json"):
            self.assertNotIn("GIZLI-ANAHTAR", path.read_text(encoding="utf-8"))
        self.assertNotIn("GIZLI-ANAHTAR", repr(SETTINGS))

    def test_cached_answer_used_when_offline(self):
        real = self.findings[0]
        reply = {"aciklamalar": [{"rule": real["rule"], "line": real["line"], "neden": "a", "duzeltme": "b"}]}
        llm.explain_findings(CODE, self.findings, SETTINGS, transport=transport_returning(reply))

        def offline(*_):
            raise llm.LLMError("Sağlayıcıya ulaşılamadı.")
        out = llm.explain_findings(CODE, self.findings, SETTINGS, transport=offline)
        self.assertEqual(out.source, "kayıtlı")
        self.assertTrue(out.data["aciklamalar"])

    def test_offline_without_cache_raises(self):
        def offline(*_):
            raise llm.LLMError("Sağlayıcıya ulaşılamadı.")
        with self.assertRaises(llm.LLMError):
            llm.explain_findings(CODE, self.findings, SETTINGS, transport=offline)

    def test_missing_key_and_oversized_code(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(llm.LLMError):
            llm.explain_findings(CODE, self.findings, llm.Settings(api_key=""), transport=transport_returning({}))
        with self.assertRaises(llm.LLMError):
            llm.explain_findings("x = 1\n" * 10_000, self.findings, SETTINGS, transport=transport_returning({}))


class CandidateTests(CacheIsolation):
    def test_verification_statuses(self):
        reply = {"adaylar": [
            {"line": 3, "alinti": "for i in range(n):", "kategori": "Performance", "baslik": "x", "neden": "y", "duzeltme": "z"},
            {"line": 10, "alinti": "return db.execute(query)", "kategori": "Security", "baslik": "x", "neden": "y", "duzeltme": "z"},
            {"line": 3, "alinti": "olmayan kod parçası", "kategori": "Style", "baslik": "x", "neden": "y", "duzeltme": "z"},
            {"line": 999, "alinti": "x", "kategori": "Style", "baslik": "x", "neden": "y", "duzeltme": "z"},
        ]}
        only_sql = [f for f in self.findings if f["rule"] == "SEC104"]
        out = llm.suggest_candidates(CODE, only_sql, SETTINGS, transport=transport_returning(reply))
        self.assertEqual(out.data["reddedilen"], 2)  # uydurma alıntı + geçersiz satır
        statuses = {a["line"]: a["status"] for a in out.data["adaylar"]}
        self.assertEqual(statuses, {3: "unverified", 10: "ast_overlap"})


class FixTests(CacheIsolation):
    def test_good_fix_passes_static_verification(self):
        fixed = CODE.replace("    result = []\n    for i in range(n):\n        result = result + [i * 2]\n    return result\n",
                             "    return [i * 2 for i in range(n)]\n")
        out = llm.propose_fix(CODE, self.findings, SETTINGS,
                              transport=transport_returning({"kod": f"```python\n{fixed}```", "degisiklikler": ["Liste üreteci"]}))
        check = out.data["dogrulama"]
        names = {c["kontrol"]: c["gecti"] for c in check["checks"]}
        self.assertTrue(names["Geçerli Python"] and names["Fonksiyon/sınıf adları korundu"] and names["Bulgular azaldı"])

    def test_broken_or_renaming_fix_is_rejected(self):
        broken = llm.propose_fix(CODE, self.findings, SETTINGS, transport=transport_returning({"kod": "def build(:\n"}))
        self.assertFalse(broken.data["dogrulama"]["verified"])
        renamed = llm.propose_fix(CODE, self.findings, SETTINGS,
                                  transport=transport_returning({"kod": "def other(n):\n    return n\n"}))
        self.assertFalse(renamed.data["dogrulama"]["verified"])

    def test_fix_that_adds_new_finding_is_rejected(self):
        worse = CODE + "\ndef extra(v):\n    return eval(v)\n"
        out = llm.propose_fix(CODE, self.findings, SETTINGS, transport=transport_returning({"kod": worse}))
        self.assertFalse(out.data["dogrulama"]["verified"])

    def test_missing_code_field_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.propose_fix(CODE, self.findings, SETTINGS, transport=transport_returning({"x": 1}))


class ProviderTests(unittest.TestCase):
    def test_http_error_messages_are_turkish_and_keyless(self):
        for code in (400, 401, 403, 404, 429, 503):
            message = llm._http_message(code)
            self.assertNotIn("http", message.lower().replace("http hatası", ""))
        self.assertIn("429", llm._http_message(429))

    def test_list_models_filters_generate_content(self):
        data = {"models": [{"name": "models/a", "supportedGenerationMethods": ["generateContent"]},
                           {"name": "models/b", "supportedGenerationMethods": ["embedContent"]}]}
        self.assertEqual(llm.list_models(SETTINGS, transport=lambda *_: data), ["a"])

    def test_openai_compatible_provider_shape(self):
        calls = []

        def transport(url, headers, body):
            calls.append((url, headers, body))
            return {"choices": [{"message": {"content": '{"ok": 1}'}}]}
        groq = llm.Settings(provider="Groq", api_key="k")
        self.assertEqual(llm._complete(groq, "s", "u", transport), '{"ok": 1}')
        self.assertTrue(calls[0][0].endswith("/chat/completions"))
        self.assertEqual(calls[0][1]["Authorization"], "Bearer k")


class ResilienceTests(CacheIsolation):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(llm, "_sleep", lambda _s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        real = self.findings[0]
        self.reply = {"aciklamalar": [{"rule": real["rule"], "line": real["line"], "neden": "a", "duzeltme": "b"}]}

    def _flaky(self, failing_models, code=503):
        seen = []

        def transport(url, headers, body):
            model = url.split("/models/")[1].split(":")[0]
            seen.append(model)
            if model in failing_models:
                raise llm.LLMError("yoğun", retryable=code >= 500)
            return gemini_reply(self.reply)
        return transport, seen

    def test_retries_same_model_then_succeeds(self):
        transport, seen = self._flaky(set())
        attempts = {"n": 0}
        inner = transport

        def once_busy(url, headers, body):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise llm.LLMError("yoğun", retryable=True)
            return inner(url, headers, body)
        out = llm.explain_findings(CODE, self.findings, SETTINGS, transport=once_busy)
        self.assertEqual(out.model, "test-model")
        self.assertEqual(attempts["n"], 2)

    def test_falls_back_to_other_model_and_reports_it(self):
        transport, seen = self._flaky({"test-model"})
        out = llm.explain_findings(CODE, self.findings, SETTINGS, transport=transport)
        self.assertEqual(out.model, "gemini-2.5-flash")
        self.assertEqual(seen.count("test-model"), len(llm.RETRY_DELAYS) + 1)

    def test_auth_error_does_not_fall_back(self):
        seen = []

        def transport(url, headers, body):
            seen.append(url)
            raise llm.LLMError("API anahtarı geçersiz (401).", retryable=False)
        with self.assertRaises(llm.LLMError) as ctx:
            llm.explain_findings(CODE, self.findings, SETTINGS, transport=transport)
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(len(seen), 1)

    def test_all_models_busy_raises_original_error(self):
        transport, _ = self._flaky({"test-model", "gemini-2.5-flash", "gemini-2.5-flash-lite"})
        with self.assertRaises(llm.LLMError):
            llm.explain_findings(CODE, self.findings, SETTINGS, transport=transport)


if __name__ == "__main__":
    unittest.main()
