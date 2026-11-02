"""Arayüz duman testi: gerçek Streamlit olmadan, sahte bir `streamlit` modülüyle app.py'yi çalıştırır.

Amaç sekmelerin (Hibrit İnceleme, Proje ve Değerlendirme, Katmanlar ve Sunum Rehberi) istisna fırlatmadan çizildiğini doğrulamaktır;
görsel doğruluğu ya da gerçek Streamlit davranışını kanıtlamaz.
"""
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


_WIDGETS = {"button", "toggle", "checkbox", "text_area", "text_input", "multiselect", "selectbox", "slider",
            "file_uploader"}


class _Stub:
    """Sütun/sekme yer tutucusu: `with` ile kullanılır, girdi bileşenlerini sahte modüle yönlendirir."""

    def __init__(self, owner=None):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        if name in _WIDGETS and self.owner is not None:
            return getattr(self.owner, name)
        return lambda *args, **kwargs: None


class FakeStreamlit(types.ModuleType):
    def __init__(self, pressed=(), checked=False):
        super().__init__("streamlit")
        self.session_state = {}
        self.pressed = set(pressed)
        self.checked = checked
        self.log = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.log.append((name, args))

        return record

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_Stub(self) for _ in range(count)]

    def tabs(self, names):
        return [_Stub(self) for _ in names]

    def expander(self, *args, **kwargs):
        return _Stub()

    def spinner(self, *args, **kwargs):
        return _Stub()

    def button(self, label, *args, **kwargs):
        pressed = label in self.pressed and not kwargs.get("disabled")
        if pressed and kwargs.get("on_click"):
            kwargs["on_click"]()
        return pressed

    def toggle(self, label, value=False, **kwargs):
        return value

    def checkbox(self, label, value=False, **kwargs):
        return self.checked or value

    def text_area(self, label, value="", key=None, **kwargs):
        if key is None:
            return value
        return self.session_state.setdefault(key, value)

    def text_input(self, label, value="", **kwargs):
        return value

    def multiselect(self, label, options, default=None, **kwargs):
        return list(default or [])

    def selectbox(self, label, options, **kwargs):
        return options[0]

    def slider(self, label, low, high, value=None, **kwargs):
        return value

    def file_uploader(self, *args, **kwargs):
        return None

    def logged(self, name):
        return [args for call, args in self.log if call == name]


def _run_app(fake):
    components = types.ModuleType("streamlit.components")
    components_v1 = types.ModuleType("streamlit.components.v1")
    components_v1.html = lambda *args, **kwargs: None
    components.v1 = components_v1
    fake.components = components
    modules = {"streamlit": fake, "streamlit.components": components, "streamlit.components.v1": components_v1}
    # sys.modules'ı tümden geri yüklemek numpy gibi kütüphaneleri ikinci kez yüklemeye çalışır;
    # bu yüzden yalnızca sahte streamlit girdilerini ekleyip çıkarıyoruz.
    sys.modules.update(modules)
    try:
        runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
    finally:
        for name in modules:
            sys.modules.pop(name, None)


class AppSmokeTests(unittest.TestCase):
    def test_first_load_renders_every_tab(self):
        fake = FakeStreamlit()
        _run_app(fake)
        self.assertEqual(fake.logged("error"), [])
        tree = " ".join(str(a) for a in fake.logged("code"))
        self.assertIn("FunctionDef", tree)

    def test_review_fix_and_impact_flow(self):
        fake = FakeStreamlit(pressed={"Kodu analiz et", "Seçili düzeltmeleri uygula"}, checked=True)
        _run_app(fake)
        self.assertTrue(fake.session_state.get("hy_after"))
        self.assertEqual(fake.logged("error"), [])
        _run_app(fake)  # ikinci çalıştırma: Sonra sürümü, rozetler ve sonuç kartı aynı durumu kullanır
        self.assertEqual(fake.logged("error"), [])
        self.assertTrue(any("Kısmen doğrulandı" in str(a) for a in fake.logged("info")))

    def test_dynamic_tab_shows_benchmark_result(self):
        fake = FakeStreamlit(pressed={"İzole ölçümü başlat"}, checked=True)
        result = {"ok": True, "before": {"median_ms": 2.0, "median_peak_bytes": 2048},
                  "after": {"median_ms": 1.0, "median_peak_bytes": 1024}}
        with mock.patch("quality_studio.benchmark.benchmark", return_value=result):
            _run_app(fake)
        self.assertEqual(fake.logged("error"), [])
        self.assertTrue(fake.session_state.get("bench_result"))

    def test_dynamic_tab_demo_and_local_mode_run(self):
        fake = FakeStreamlit(pressed={"Hazır örneği yükle (döngüde liste birleştirme)"}, checked=True)
        _run_app(fake)
        self.assertIn("def build", fake.session_state["dyn_before"])
        self.assertEqual(fake.session_state["bench_args"], "[3000]")
        self.assertEqual(fake.logged("error"), [])

    def test_dynamic_tab_shows_static_vs_dynamic_table(self):
        fake = FakeStreamlit(pressed={"İzole ölçümü başlat"}, checked=True)
        result = {"ok": True, "mode": "docker", "before": {"median_ms": 4.0, "median_peak_bytes": 4096},
                  "after": {"median_ms": 1.0, "median_peak_bytes": 1024}}
        with mock.patch("quality_studio.benchmark.benchmark", return_value=result):
            _run_app(fake)
        tables = [str(a) for a in fake.logged("table")]
        self.assertTrue(any("Dinamik (çalıştırma)" in t for t in tables))
        self.assertEqual(fake.logged("error"), [])

    def test_hybrid_without_key_asks_for_key_but_static_layer_works(self):
        fake = FakeStreamlit(pressed={"Kodu analiz et"})
        with mock.patch.dict("os.environ", {}, clear=True):
            _run_app(fake)
        infos = " ".join(str(a) for a in fake.logged("info"))
        self.assertIn("API anahtar", infos)
        self.assertTrue(any("Statik" in str(a) for a in fake.logged("markdown")))
        self.assertEqual(fake.logged("error"), [])

    def _llm_data(self):
        return {"aciklamalar": {}, "reddedilen": 0, "reddedilen_test": 0, "fonksiyon": "process",
                "kod": "def process(value):\n    return 1\n", "degisiklikler": ["a"],
                "dogrulama": None,
                "test_girdileri": [{"label": "tipik", "args": [1], "kwargs": {}}]}

    def test_hybrid_llm_then_measure_flow_with_mocked_layers(self):
        from quality_studio import llm
        button = "Hibrit incelemeyi çalıştır (LLM açıklar, düzeltir, test girdisi önerir)"
        fake = FakeStreamlit(pressed={"Kodu analiz et", button, "Önerilen düzeltmeyi ölçerek doğrula"}, checked=True)
        fake.session_state["llm_key"] = "test-anahtari"
        result = {"ok": True, "mode": "docker", "cases_total": 1, "cases_same": 1, "headline_case": "tipik",
                  "cases": [{"label": "tipik", "same": True, "timed": True, "kind": "ok",
                             "before": {"median_ms": 2.0, "median_peak_bytes": 2048},
                             "after": {"median_ms": 1.0, "median_peak_bytes": 1024}}],
                  "before": {"median_ms": 2.0, "median_peak_bytes": 2048},
                  "after": {"median_ms": 1.0, "median_peak_bytes": 1024}}
        with mock.patch("quality_studio.hybrid.review_and_fix",
                        return_value=llm.Outcome(self._llm_data(), "canlı", "m")), \
                mock.patch("quality_studio.benchmark.benchmark", return_value=result):
            _run_app(fake)
            self.assertEqual(fake.session_state["hy_review"]["source"], "canlı")
            self.assertIn("return 1", fake.session_state["hy_after"])
            self.assertIn("tipik", fake.session_state["hy_cases"])
            _run_app(fake)  # ikinci tur: ölçüm düğmesi (Sonra sürümü artık dolu)
        self.assertEqual(fake.session_state["hy_bench"]["sources"][2], "process")
        self.assertTrue(fake.session_state["hy_bench"]["result"]["ok"])
        # sahte düzeltme find_user'ı sildiği için statik doğrulama kasıtlı olarak reddeder; başka hata olmamalı
        self.assertEqual([e for e in fake.logged("error") if "statik doğrulama" not in str(e).lower()], [])

    def test_hybrid_reports_unmeasurable_script_instead_of_failing(self):
        fake = FakeStreamlit(pressed={"Kodu analiz et"}, checked=True)
        fake.session_state["editor"] = "x = 1\nprint(x)\n"
        fake.session_state["hy_after"] = "x = 2\nprint(x)\n"
        _run_app(fake)
        warnings = " ".join(str(a) for a in fake.logged("warning"))
        self.assertIn("Dinamik ölçüm yapılamıyor", warnings)
        self.assertIn("fonksiyon yok", warnings)

    def test_hybrid_invalid_test_input_json_is_reported_not_run(self):
        fake = FakeStreamlit(pressed={"Kodu analiz et", "Önerilen düzeltmeyi ölçerek doğrula"}, checked=True)
        fake.session_state["hy_after"] = "def process(value):\n    return 1\n"
        fake.session_state["hy_cases"] = "[bozuk"
        with mock.patch("quality_studio.benchmark.benchmark") as bench:
            _run_app(fake)
        bench.assert_not_called()
        self.assertTrue(any("Geçersiz JSON" in str(a) for a in fake.logged("error")))


if __name__ == "__main__":
    unittest.main()
