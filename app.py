"""Code Quality Studio: tek Streamlit arayüzü (API anahtarı veya dış servis gerekmez).

Çalıştırma:  streamlit run app.py
Gönderilen kod ayrıştırılır, çalıştırılmaz (yalnızca isteğe bağlı Docker benchmark'ı hariç).
"""
from __future__ import annotations

import ast
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from quality_studio.astview import category_counts, finding_nodes, rule_table, tree_text
from quality_studio.benchmark import BenchmarkOptions, benchmark, docker_status, summarize
from quality_studio.codeview import render_code
from quality_studio.compare import evaluate_dataset, load_dataset
from quality_studio.diffview import render_side_by_side
from quality_studio.engine import analyze
from quality_studio.evaluation import evaluate
from quality_studio import hybrid, llm
from quality_studio.impact import impact, patch_effects
from quality_studio.models import CATEGORY_TR, CONFIDENCE_TR, SEVERITY_TR
from quality_studio.project_scan import analyzed, project_summary, scan_archive
from quality_studio.refactor import apply, suggest, unified_diff
from quality_studio.report import to_html, to_json, to_sarif

MAX_SOURCE_BYTES = 250_000
SEVERITIES = ["critical", "high", "medium", "low", "info"]
LAYER_LABEL = {"AST": "Statik (AST)", "ruff": "Statik (ruff)", "bandit": "Statik (bandit)"}
EFFECT_TR = {"negligible": "önemsiz", "small": "küçük", "medium": "orta", "large": "büyük"}
EXAMPLE = '''def find_user(user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    return db.execute(query)


def process(value):
    try:
        if value == None:
            return eval("1+2")
    except:
        pass
'''

st.set_page_config(page_title="Code Quality Studio", page_icon="◈", layout="wide")
st.markdown(
    """<style>
    .block-container{max-width:1240px;padding-top:2rem}
    [data-testid="stMetric"]{background:#121f2e;border:1px solid #2a3a50;border-radius:12px;padding:14px}
    </style>""",
    unsafe_allow_html=True,
)


def _severity_tr(value: str) -> str:
    return SEVERITY_TR.get(value, value)


def _category_tr(value: str) -> str:
    return CATEGORY_TR.get(value, value)


def _header() -> None:
    st.title("◈ Code Quality Studio")
    st.caption("Python kodu için hibrit kalite inceleme aracı: statik analiz (AST), dinamik analiz (izole çalıştırma) "
               "ve LLM açıklaması. Skor ve YZ göstergeleri sezgiseldir, kanıt değildir.")
    static_col, dynamic_col, llm_col = st.columns(3)
    static_col.success("**Statik katman:** hazır. Kod çalıştırılmadan AST ile incelenir.")
    status = docker_status()
    if status["daemon"]:
        dynamic_col.success("**Dinamik katman:** Docker çalışıyor. Süre ve bellek ölçümü yapılabilir.")
    elif status["cli"]:
        dynamic_col.warning("**Dinamik katman:** Docker kurulu ama çalışmıyor. Docker Desktop'ı açın.")
    else:
        dynamic_col.warning("**Dinamik katman:** Docker bulunamadı. Yerel yedek mod kullanılabilir.")
    settings = _llm_settings()
    if settings.resolved_key():
        llm_col.success(f"**LLM katmanı:** anahtar hazır ({settings.provider} · {settings.resolved_model()}).")
    else:
        llm_col.info("**LLM katmanı:** API anahtarı girilmedi. Hibrit İnceleme sekmesinden girebilirsiniz.")


def _read_upload(upload, limit: int = MAX_SOURCE_BYTES) -> str | None:
    if upload.size > limit:
        st.error(f"Dosya {limit // 1000} KB sınırını aşıyor")
        return None
    try:
        return upload.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        st.error("Dosya UTF-8 olmalı")
        return None


def _code_viewer(code: str, findings: list[dict]) -> None:
    lines = sorted({f["line"] for f in findings})
    focus = st.selectbox("Bir bulguya git", [None, *lines],
                         format_func=lambda n: "Tüm satırlar" if n is None else f"Satır {n}")
    height = min(650, max(220, 100 + len(code.splitlines()) * 23))
    components.html(render_code(code, findings, focus_line=focus), height=height, scrolling=True)


def _impact_metrics(effect: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Kalite skoru", f"{effect['score_before']} → {effect['score_after']}", delta=effect["score_delta"])
    c2.metric("Bulgu sayısı", f"{effect['findings_before']} → {effect['findings_after']}",
              delta=effect["findings_after"] - effect["findings_before"], delta_color="inverse")
    c3.metric("Çözülen", effect["resolved"])
    c4.metric("Yeni sorun", effect["introduced"], delta=effect["introduced"], delta_color="inverse")


def _impact_tables(effect: dict) -> None:
    labels = {"nonblank_lines": "Boş olmayan satır", "branches": "Dallanma", "loops": "Döngü",
              "functions": "Fonksiyon", "complexity_total": "Karmaşıklık (toplam)", "ast_nodes": "AST düğümü"}
    frame = pd.DataFrame([{"Ölçüt": label, "Önce": effect["before"][key], "Sonra": effect["after"][key]}
                          for key, label in labels.items()]).set_index("Ölçüt")
    st.bar_chart(frame)
    st.caption("Yalnızca statik sayımlar. Daha az satır veya düğüm, daha hızlı ya da daha iyi kod demek değildir.")
    for key, title in (("resolved_rules", "Çözülen bulgular"), ("introduced_rules", "Yeni bulgular")):
        if effect[key]:
            st.markdown(f"**{title}**")
            st.dataframe(effect[key], hide_index=True)


def _impact_downloads(before: str, after: str, effect: dict) -> None:
    with st.expander("Birleşik diff"):
        st.code(unified_diff(before, after) or "Değişiklik yok", language="diff")
    st.download_button("İyileştirilmiş .py dosyasını indir", after, file_name="improved.py", mime="text/x-python")
    st.download_button("HTML raporunu indir", to_html((before, analyze(before)), (after, analyze(after))),
                       file_name="quality_report.html", mime="text/html")
    st.download_button("Etki JSON'unu indir", json.dumps(effect, indent=2, ensure_ascii=False),
                       file_name="impact.json")


def _effect_label(effect: dict | None) -> str:
    if effect is None:
        return ""
    return f" · skor {effect['score_before']} → {effect['score_after']} ({effect['score_delta']:+d})"


def _patch_preview(source: str, preview: str) -> None:
    effect = impact(source, preview)
    if not effect["valid"]:
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Uygulanırsa skor", f"{effect['score_before']} → {effect['score_after']}", delta=effect["score_delta"])
    c2.metric("Çözülen bulgu", effect["resolved"])
    c3.metric("Yeni sorun", effect["introduced"], delta=effect["introduced"], delta_color="inverse")


def _patch_panel(source: str, patches: list[dict]) -> str | None:
    if not patches:
        st.write("Güvenli otomatik yama yok. Diğer bulgular elle incelenmeli.")
        return None
    st.caption("Yamaların sözdizimi denetlenir ama davranışı doğrulanmaz. Her uyarıyı okuyun. "
               "Her yamanın yanındaki skor yalnızca o yamanın statik etkisidir.")
    effects = patch_effects(source, patches)
    chosen = []
    for index, patch in enumerate(patches):
        label = f"S{patch['line']} · {patch['rule']} · {patch['description']}{_effect_label(effects[index])}"
        if st.checkbox(label, key=f"patch_{index}_{hash(source)}"):
            chosen.append(index)
            st.caption("⚠ " + patch["caution"])
    preview = None
    if chosen:
        try:
            preview = apply(source, patches, chosen)
        except (ValueError, SyntaxError) as exc:
            st.error(str(exc))
        else:
            _patch_preview(source, preview)
    if not st.button("Seçili düzeltmeleri uygula", disabled=preview is None):
        return None
    return preview


def _load_editor() -> str:
    upload = st.file_uploader(".py dosyası yükle (isteğe bağlı)", type=["py"], key="source_upload")
    if upload is not None:
        signature = (upload.name, upload.size)
        if st.session_state.get("upload_signature") != signature:
            text = _read_upload(upload)
            if text is not None:
                st.session_state["editor"] = text
                st.session_state["upload_signature"] = signature
    st.session_state.setdefault("editor", EXAMPLE)
    return st.text_area("Python kaynak kodu", key="editor", height=280)


def _run_analysis(code: str) -> bool:
    try:
        result = analyze(code)
    except ValueError as exc:
        st.error(str(exc))
        return False
    st.session_state.update(snapshot=code, result=result, patches=suggest(code) if result.valid else [])
    st.session_state.pop("after", None)
    return True


def _summary_metrics(result) -> None:
    metrics = result.metrics
    cols = st.columns(5)
    cols[0].metric("Bulgu", len(result.findings))
    cols[1].metric("Sezgisel skor", metrics["score"])
    cols[2].metric("Karmaşıklık (en büyük fonksiyon)", metrics["complexity_max_function"])
    cols[3].metric("YZ göstergesi / 100 satır", metrics["ai_indicators_per_100_lines"])
    cols[4].metric("Bastırılan (noqa)", result.suppressed)
    st.caption("YZ göstergeleri zayıf, olasılıksal sinyallerdir (ör. kodu tekrar eden yorumlar, gereksiz geçici "
               "değişkenler). Yazarlığı kanıtlamazlar ve skoru etkilemezler.")


def _current_code() -> str:
    return st.session_state.get("snapshot") or st.session_state.get("editor", EXAMPLE)


def _static_intro() -> None:
    st.subheader("Statik Analiz: kodu çalıştırmadan inceleme")
    st.markdown(
        "Statik analiz, kodu **çalıştırmadan** yalnızca yapısına bakarak inceler. Python'un `ast` modülü kodu bir "
        "**ağaca** (AST) çevirir, kurallar bu ağaçta gezer ve sorunlu desenleri bulur.")
    with st.expander("Nasıl çalışır? (adım adım)", expanded=True):
        st.markdown(
            "1. **Kod → AST:** `ast.parse` kodu düğümlere böler (fonksiyon, atama, çağrı...).\n"
            "2. **Kurallar gezer:** 36 kural ağacı dolaşır (güvenlik, performans, kod kokusu, gereksiz kod, YZ göstergesi).\n"
            "3. **Bulgu üretilir:** her bulgu satır, kategori, önem, güven, neden ve düzeltme bilgisiyle gelir.\n"
            "4. **Skor ve metrikler:** karmaşıklık, satır ve düğüm sayıları ile sezgisel bir skor hesaplanır.\n"
            "5. **Güvenlik:** kod hiçbir zaman içe aktarılmaz veya çalıştırılmaz.")
    with st.expander("Statik analiz neyi göremez?"):
        st.markdown(
            "- **Veri akışını** izlemez: `run = eval; run(x)` gibi takma adlar kaçabilir.\n"
            "- Verinin **gerçek boyutunu** bilmez: iç içe döngü sadece bir ipucudur.\n"
            "- Kodun **gerçek hızını ve bellek kullanımını** ölçemez; bunun için **Dinamik Analiz** sekmesi vardır.")


def _static_tree(code: str, findings: list[dict]) -> None:
    st.markdown("#### 1) Kod → AST ağacı")
    st.caption("⚠ ile işaretli düğümler, bir kuralın bulgu ürettiği yerlerdir.")
    only_flagged = st.checkbox("Yalnızca bulgu çıkan düğümleri ve üst dallarını göster", value=True, key="ast_only")
    st.code(tree_text(code, findings, only_flagged=only_flagged), language=None)


def _static_findings(code: str, findings: list[dict]) -> None:
    st.markdown("#### 2) Hangi kural hangi düğümde tetiklendi?")
    if findings:
        st.dataframe(finding_nodes(code, findings), hide_index=True)
        st.markdown("#### 3) Kategorilere göre dağılım")
        st.bar_chart(pd.Series(category_counts(findings), name="Bulgu"))
    else:
        st.success("Bu kodda bulgu çıkmadı.")


def _static_catalog(findings: list[dict]) -> None:
    st.markdown("#### 4) Kural kataloğu")
    rows = rule_table(findings)
    only_hits = st.checkbox("Yalnızca bu kodda tetiklenen kurallar", value=False, key="catalog_hits")
    shown = [r for r in rows if r["Bu kodda"] > 0] if only_hits else rows
    st.dataframe(shown, hide_index=True)
    st.caption(f"Toplam {len(rows)} kural. Her kuralın kısa açıklaması ve düzeltme önerisi tabloda yer alır.")


def _static_tab() -> None:
    _static_intro()
    code = _current_code()
    st.caption(f"Analiz edilen kod: **Kod İnceleme** sekmesindeki editör ({len(code.splitlines())} satır).")
    try:
        result = analyze(code)
    except ValueError as exc:
        st.error(str(exc))
        return
    if not result.valid:
        st.error("Geçersiz Python sözdizimi: " + result.findings[0].why)
        return
    cols = st.columns(4)
    cols[0].metric("Bulgu", len(result.findings))
    cols[1].metric("Sezgisel skor", result.metrics["score"])
    cols[2].metric("AST düğümü", result.metrics["ast_nodes"])
    cols[3].metric("Fonksiyon", result.metrics["functions"])
    _static_tree(code, result.issues)
    _static_findings(code, result.issues)
    _static_catalog(result.issues)


MODE_DOCKER = "Docker (izole, önerilen)"
MODE_LOCAL = "Yerel yedek (izole değil, yalnızca güvendiğiniz kod)"
DEMO_BEFORE = """def build(n):
    result = []
    for i in range(n):
        result = result + [i * 2]
    return result
"""
DEMO_AFTER = """def build(n):
    return [i * 2 for i in range(n)]
"""


def _dynamic_intro() -> None:
    st.subheader("Dinamik Analiz: kodu çalıştırarak ölçme")
    st.markdown(
        "Statik analiz bir şeyi **sorun diye işaretler**. Dinamik analiz ise düzeltmenin **gerçekten işe yarayıp "
        "yaramadığını kanıtlar**: iki sürümü aynı girdiyle çalıştırır ve ölçer.")
    st.markdown(
        "**Nasıl çalışır?**\n"
        "1. **Çıktı eşitliği:** Önce ve Sonra aynı girdiyle çalıştırılır; sonuçlar farklıysa ölçüm yapılmaz "
        "(yama davranışı bozmuş demektir).\n"
        "2. **Süre:** Isınma turlarından sonra seçtiğiniz sayıda tekrar edilir, medyan alınır.\n"
        "3. **Bellek:** Süreden ayrı turlarda `tracemalloc` ile en yüksek bellek ayırma ölçülür.")
    st.table(pd.DataFrame([
        {"Kriter": "Kod çalışır mı?", "Statik analiz": "Hayır, yalnızca AST", "Dinamik analiz": "Evet, ayrı süreçte"},
        {"Kriter": "Ne bulur?", "Statik analiz": "Güvenlik, kod kokusu, desen", "Dinamik analiz": "Süre, bellek, çıktı farkı"},
        {"Kriter": "Hız", "Statik analiz": "Anında", "Dinamik analiz": "Birkaç saniye"},
        {"Kriter": "Neyi göremez?", "Statik analiz": "Gerçek hız ve çalışma davranışı",
         "Dinamik analiz": "Yalnızca verdiğiniz girdi için geçerli"},
    ]).set_index("Kriter"))
    with st.expander("Güvenlik: iki çalışma modu"):
        st.table(pd.DataFrame([
            {"Önlem": "Ağ erişimi", "Docker modu": "Kapalı", "Yerel yedek mod": "Kapatılmaz"},
            {"Önlem": "Dosya sistemi", "Docker modu": "Salt-okunur", "Yerel yedek mod": "Kapatılmaz"},
            {"Önlem": "Yetkiler / kullanıcı", "Docker modu": "Tümü kapalı, root değil", "Yerel yedek mod": "Sizin yetkiniz"},
            {"Önlem": "Süre sınırı", "Docker modu": "Var", "Yerel yedek mod": "Var"},
            {"Önlem": "Bellek / CPU sınırı", "Docker modu": "256 MB, 1 CPU", "Yerel yedek mod": "Linux/macOS'ta var, Windows'ta yok"},
        ]).set_index("Önlem"))
        st.caption("Docker mükemmel bir güvenlik sınırı değildir. Yerel yedek mod ise gerçek bir izolasyon sağlamaz: "
                   "yalnızca kendi yazdığınız ya da güvendiğiniz kodla kullanın.")


def _docker_checklist() -> None:
    status = docker_status()
    steps = (("Docker komutu kurulu", status["cli"]), ("Docker servisi çalışıyor", status["daemon"]),
             ("Python imajı indirilmiş", status["image"]))
    cols = st.columns(3)
    for col, (label, ok) in zip(cols, steps):
        (col.success if ok else col.warning)(("✓ " if ok else "✗ ") + label)
    if status["daemon"] and not status["image"]:
        st.caption("İmaj ilk ölçümde otomatik indirilir (internet gerekir, birkaç dakika sürebilir).")
    if not status["daemon"]:
        st.caption("Docker hazır değilse aşağıdan **Yerel yedek** modunu seçebilirsiniz.")


def _use_review_versions() -> None:
    st.session_state["dyn_before"] = st.session_state.get("snapshot", "")
    st.session_state["dyn_after"] = st.session_state.get("after_edit") or st.session_state.get("after", "")


def _load_demo() -> None:
    st.session_state["dyn_before"] = DEMO_BEFORE
    st.session_state["dyn_after"] = DEMO_AFTER
    st.session_state["bench_args"] = "[3000]"


def _dynamic_sources() -> tuple[str, str]:
    snapshot = st.session_state.get("snapshot", _current_code())
    st.session_state.setdefault("dyn_before", snapshot)
    st.session_state.setdefault("dyn_after", st.session_state.get("after_edit") or snapshot)
    b1, b2 = st.columns(2)
    b1.button("Kod İnceleme sekmesindeki sürümleri al", on_click=_use_review_versions)
    b2.button("Hazır örneği yükle (döngüde liste birleştirme)", on_click=_load_demo)
    left, right = st.columns(2)
    before = left.text_area("Önce sürümü", key="dyn_before", height=220)
    after = right.text_area("Sonra sürümü", key="dyn_after", height=220)
    return before, after


def _benchmark_inputs(before: str, after: str) -> dict | None:
    def top_level(code: str) -> set[str]:
        return {n.name for n in ast.parse(code).body if isinstance(n, ast.FunctionDef)}

    try:
        common = sorted(top_level(before) & top_level(after))
    except SyntaxError:
        st.error("Sürümlerden birinde sözdizimi hatası var.")
        return None
    if not common:
        st.info("Ölçüm için iki sürümde de aynı isimli bir üst düzey fonksiyon olmalı.")
        return None
    function = st.selectbox("Ölçülecek fonksiyon", common, key="bench_fn")
    args_text = st.text_input("Konumsal argümanlar (JSON dizisi)", value="[]", key="bench_args")
    kwargs_text = st.text_input("İsimli argümanlar (JSON nesnesi)", value="{}", key="bench_kwargs")
    repeats = st.slider("Tekrar sayısı", 3, 40, 15, key="bench_repeats")
    try:
        args, kwargs = json.loads(args_text), json.loads(kwargs_text)
    except ValueError as exc:
        st.error(f"Geçersiz JSON: {exc}")
        return None
    return {"function": function, "args": args, "kwargs": kwargs, "repeats": repeats}


def _static_vs_dynamic(before: str, after: str, result: dict) -> None:
    """Hocaya gösterilecek özet: statik sonuç ile dinamik sonuç yan yana."""
    st.markdown("#### Statik ve dinamik sonuç yan yana")
    summary = summarize(result)
    effect = impact(before, after)
    rows = []
    if effect["valid"]:
        rows.append({"Katman": "Statik (AST)", "Soru": "Kod kalitesi iyileşti mi?",
                     "Sonuç": f"Skor {effect['score_before']} → {effect['score_after']}, "
                              f"{effect['resolved']} bulgu çözüldü, {effect['introduced']} yeni bulgu",
                     "Kanıt gücü": "Gösterge (kural tabanlı)"})
    speed = f"{summary['speedup']:.2f}×" if summary["speedup"] else "ölçülemedi"
    rows.append({"Katman": "Dinamik (çalıştırma)", "Soru": "Davranış aynı mı, daha hızlı mı?",
                 "Sonuç": f"Çıktılar eşleşti; hız oranı {speed} ({summary['time_verdict']})",
                 "Kanıt gücü": "Ölçüm (yalnızca bu girdi için)"})
    st.table(pd.DataFrame(rows).set_index("Katman"))


def _benchmark_result(result: dict, before: str = "", after: str = "") -> None:
    if not result.get("ok"):
        st.error(result.get("error", "Ölçüm başarısız"))
        if "before_outcome" in result:
            st.caption(f"Önce çıktısı: {result['before_outcome']} · Sonra çıktısı: {result['after_outcome']}")
        return
    summary = summarize(result)
    b, a = result["before"], result["after"]
    mode_text = "Docker (izole)" if result.get("mode", "docker") == "docker" else "Yerel yedek (izole değil)"
    st.success(f"Çıktılar eşleşti. Çalışma modu: {mode_text}.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Önce (medyan)", f"{b['median_ms']:.4f} ms")
    c2.metric("Sonra (medyan)", f"{a['median_ms']:.4f} ms")
    c3.metric("Hız oranı (önce/sonra)", f"{summary['speedup']:.2f}×" if summary["speedup"] else "yok")
    m1, m2 = st.columns(2)
    m1.metric("Önce en yüksek bellek ayırma", f"{b['median_peak_bytes'] / 1024:.1f} KiB")
    m2.metric("Sonra en yüksek bellek ayırma", f"{a['median_peak_bytes'] / 1024:.1f} KiB")
    st.info(f"**Süre:** {summary['time_verdict']}. **Bellek:** {summary['memory_verdict']}. "
            "(%10'dan küçük farklar ölçüm gürültüsü sayılır.)")
    st.caption("Çıktılar yalnızca bu girdi için eşleşti. Süre ve bellek ayrı turlarda ölçüldü. "
               "tracemalloc Python ayırmalarını sayar, işlem RSS'ini değil.")
    if before and after:
        _static_vs_dynamic(before, after, result)
    st.download_button("Ölçüm JSON'unu indir", json.dumps(result, indent=2, ensure_ascii=False),
                       file_name="benchmark.json")


def _dynamic_tab() -> None:
    _dynamic_intro()
    _docker_checklist()
    before, after = _dynamic_sources()
    settings = _benchmark_inputs(before, after)
    if settings is None:
        return
    mode_label = st.selectbox("Çalışma modu", [MODE_DOCKER, MODE_LOCAL], key="bench_mode")
    mode = "docker" if mode_label == MODE_DOCKER else "yerel"
    if mode == "docker":
        consent_text, button_text = "Kodun Docker içinde çalıştırılacağını anlıyorum", "İzole ölçümü başlat"
    else:
        st.warning("Yerel yedek mod kodu bu bilgisayarda **izole olmadan** çalıştırır (ağ ve dosya erişimi açıktır). "
                   "Yalnızca kendi yazdığınız ya da tamamen güvendiğiniz kodla kullanın.")
        consent_text = "Kodun bu bilgisayarda izole olmadan çalışacağını ve güvendiğim bir kod olduğunu anlıyorum"
        button_text = "Yerel ölçümü başlat"
    consent = st.checkbox(consent_text, key="bench_consent")
    if st.button(button_text, disabled=not consent, key="bench_run"):
        with st.spinner("Çalışıyor (Docker modunda ilk çalıştırmada imaj indirilebilir)..."):
            st.session_state["bench_result"] = benchmark(
                before, after, settings["function"], settings["args"], settings["kwargs"],
                BenchmarkOptions(repeats=settings["repeats"], mode=mode))
            st.session_state["bench_sources"] = (before, after)
    if st.session_state.get("bench_result"):
        pair = st.session_state.get("bench_sources", ("", ""))
        _benchmark_result(st.session_state["bench_result"], *pair)


def _llm_settings() -> llm.Settings:
    ss = st.session_state
    return llm.Settings(provider=ss.get("llm_provider", "Google Gemini"), model=ss.get("llm_model_name", ""),
                        api_key=ss.get("llm_key", ""))


def _llm_intro() -> None:
    st.subheader("LLM katmanı: yapay zekâ ile anlama")
    st.markdown(
        "Statik analiz kuralları bilir ama **kodun ne yapmaya çalıştığını anlamaz**. LLM katmanı, **tek bir çağrıda** "
        "üç iş yapar:\n\n"
        "1. **Bulguları bu koda özgü açıklar** (kısa Türkçe, neden + nasıl düzelir).\n"
        "2. **Düzeltilmiş kod önerir** (kuralların resmi düzeltme önerilerine uymaya çalışır).\n"
        "3. **Test girdileri önerir** (sınır, tipik ve orta büyüklükte girdi); dinamik ölçüm bunları kullanır.\n\n"
        "Kuralların kaçırdığı adaylar ise ana akışın dışında, ayrı bir *ikinci görüş* bölümündedir.")
    with st.expander("Güvence zinciri: LLM'e neden körü körüne güvenmiyoruz?", expanded=True):
        st.markdown(
            "LLM hata yapabilir ve uydurabilir. Bu yüzden **söylediği her şey doğrulanır**:\n\n"
            "- **Açıklama:** yalnızca motorun gerçekten bulduğu (kural, satır) çiftleri için kabul edilir; uydurmalar atılır.\n"
            "- **Düzeltme:** geçerli Python olmalı, adlar korunmalı, bulgular azalmalı, yeni bulgu doğmamalı (**statik doğrulama**).\n"
            "- **Test girdisi ve fonksiyon adı:** JSON olarak geçerli olmalı, fonksiyon iki sürümde de bulunmalı. "
            "Doğru çıktıyı LLM'in bilmesi gerekmez: **Önce sürümü referanstır**, Sonra'nın çıktısı onunla karşılaştırılır.\n"
            "- **Davranış ve hız:** LLM'e sorulmaz, **çalıştırılarak ölçülür** (Docker ya da yerel yedek mod).\n\n"
            "Zincir: **LLM önerir → AST doğrular → çalıştırma ölçer.**")


def _llm_connection() -> tuple[llm.Settings, bool]:
    with st.expander("Bağlantı ayarları", expanded=not _llm_settings().resolved_key()):
        names = list(llm.PROVIDERS)
        st.selectbox("Sağlayıcı", names, key="llm_provider")
        st.text_input("API anahtarı (yalnızca bu oturumda bellekte tutulur, dosyaya yazılmaz)", type="password",
                      key="llm_key")
        st.text_input("Model adı (boşsa varsayılan)", key="llm_model_name",
                      help="Model adları sık değişir. Çalışmazsa 'Modelleri listele' ile geçerli bir ad seçin.")
        if st.button("Modelleri listele", key="llm_list_btn"):
            try:
                st.session_state["llm_models"] = llm.list_models(_llm_settings())
            except llm.LLMError as exc:
                st.error(str(exc))
        models = st.session_state.get("llm_models")
        if models:
            st.caption("Anahtarınızın kullanabildiği modeller (birini yukarıdaki kutuya yazın):")
            st.code(", ".join(models), language=None)
    live = st.toggle("Canlı çağrı yap (kapalıysa önce kayıtlı yanıt kullanılır)", value=True, key="llm_live")
    st.caption("Ücretsiz katmanda gönderilen kod Google tarafından ürün geliştirmede kullanılabilir. "
               "Gizli ya da özel kod göndermeyin; örnek veya kendi proje kodunuzla kullanın.")
    st.caption("**Kota:** ücretsiz katmanda model başına yaklaşık dakikada 5, günde 20 istek vardır. Hibrit inceleme düğmesi 1 istektir. "
               "Sunumdan önce örnek kodla bir kez çalıştırın; aynı kodla tekrar çağrıda 'Canlı çağrı' kapalıysa "
               "kayıtlı yanıt kullanılır ve kota harcanmaz.")
    return _llm_settings(), live


def _llm_badge(outcome: dict) -> None:
    if outcome["source"] == "canlı":
        st.caption(f"Canlı yanıt · model: {outcome['model']}")
        if outcome.get("requested") and outcome["requested"] != outcome["model"]:
            st.info(f"Seçilen model ({outcome['requested']}) yoğundu; yedek model kullanıldı: {outcome['model']}.")
    else:
        st.warning(f"Kayıtlı yanıt gösteriliyor (canlı çağrı yapılmadı). Model: {outcome['model']}")


def _llm_run(name: str, func, code: str, findings: list[dict], settings: llm.Settings, live: bool) -> bool:
    """LLM görevini çalıştırır, sonucu oturuma yazar. Başarılıysa True, hata ekrana yazılırsa False."""
    try:
        with st.spinner("LLM çalışıyor..."):
            outcome = func(code, findings, settings, live=live)
    except llm.LLMError as exc:
        st.error(str(exc))
        return False
    st.session_state[name] = {"data": outcome.data, "source": outcome.source, "model": outcome.model, "code": code,
                              "requested": settings.resolved_model()}
    return True


def _llm_fresh(name: str, code: str) -> dict | None:
    stored = st.session_state.get(name)
    if not stored:
        return None
    if stored["code"] != code:
        st.warning("Kod değişti; bu sonuç eski kod içindi. Yeniden çalıştırın.")
        return None
    return stored


STATUS_TR = {"ast_overlap": "AST ile örtüşüyor", "unverified": "Doğrulanmadı (öneri)"}


def _llm_candidates_section(code: str, findings: list[dict], settings, live: bool) -> None:
    st.markdown("#### 2) Kuralların kaçırdığı adaylar")
    if st.button("Aday sorunları öner", key="llm_cand_btn"):
        _llm_run("llm_cand", llm.suggest_candidates, code, findings, settings, live)
    stored = _llm_fresh("llm_cand", code)
    if not stored:
        return
    _llm_badge(stored)
    rows = stored["data"]["adaylar"]
    if not rows:
        st.success("LLM yeni bir aday öneremedi (ya da önerdikleri doğrulamadan geçmedi).")
    else:
        st.dataframe([{"Satır": r["line"], "Kategori": _category_tr(r["category"]), "Başlık": r["title"],
                       "Neden": r["why"], "Öneri": r["fix"], "Durum": STATUS_TR[r["status"]],
                       "Örtüşen kural": ", ".join(r["ast_rules"])} for r in rows], hide_index=True)
        st.caption("**Doğrulanmadı** etiketi: LLM'in görüşü; statik kurallar bunu teyit edemedi. Kesin bulgu sayılmaz.")
    if stored["data"]["reddedilen"]:
        st.caption(f"{stored['data']['reddedilen']} aday atıldı: satır numarası veya alıntı gerçek koda uymuyordu.")


def _project_tab() -> None:
    st.write("Bir Python projesinin ZIP dosyasını yükleyin. Dosyalar statik olarak incelenir; "
             "`__pycache__`, sanal ortamlar ve `.git` atlanır.")
    archive = st.file_uploader("Proje ZIP dosyası", type=["zip"], key="project_zip")
    if archive is None or not st.button("Projeyi tara"):
        return
    try:
        files = scan_archive(archive.getvalue())
    except ValueError as exc:
        st.error(str(exc))
        return
    summary = project_summary(files)
    cols = st.columns(4)
    cols[0].metric("İncelenen dosya", f"{summary['analyzed_files']}/{summary['files']}")
    cols[1].metric("Bulgu", summary["issues"])
    cols[2].metric("Yüksek öncelik", summary["high_priority"])
    cols[3].metric("Sözdizimi hatalı dosya", summary["syntax_error_files"])
    st.dataframe([{k: v for k, v in f.items() if k in ("file", "line", "rule", "severity", "category", "message")}
                  for f in summary["findings"]], hide_index=True)
    entries = analyzed(files)
    st.download_button("SARIF indir", to_sarif(entries), file_name="results.sarif.json")
    st.download_button("JSON indir", to_json(entries), file_name="project_review.json")


def _comparison_rows(pair: dict) -> list[dict]:
    rows = []
    for key, name in (("defect_density", "Kusur yoğunluğu"), ("ai_indicator_density", "YZ göstergesi yoğunluğu")):
        stat = pair[key]
        if stat["computable"]:
            rows.append({"Ölçüt": name, "Ortalama fark (a-b)": stat["mean_difference"], "%95 GA": str(stat["ci95"]),
                         "Mann-Whitney p": stat["p_value"], "Bonferroni p": stat["p_adjusted_bonferroni"],
                         "Cliff's delta": stat["cliffs_delta"],
                         "Etki büyüklüğü": EFFECT_TR.get(stat["effect_size"], stat["effect_size"])})
        else:
            rows.append({"Ölçüt": name, "Ortalama fark (a-b)": stat["reason"]})
    return rows


def _label_summary(result: dict) -> None:
    names = {"samples": "Örnek", "valid_samples": "Geçerli örnek", "syntax_errors": "Sözdizimi hatası",
             "syntax_error_rate": "Hata oranı", "nonblank_lines": "Boş olmayan satır", "findings": "Bulgu",
             "pooled_findings_per_100_lines": "100 satırda bulgu", "mean_defect_density": "Ort. kusur yoğunluğu",
             "mean_ai_indicator_density": "Ort. YZ göstergesi yoğunluğu"}
    st.markdown("**Etiket özeti**")
    st.dataframe([{"Etiket": k, **{names.get(n, n): v for n, v in s.items() if n != "category_counts"}}
                  for k, s in result["labels"].items()], hide_index=True)


def _dataset_tab() -> None:
    st.write("`etiket,kod` sütunlu bir CSV yükleyin; her satır bir örnektir (bkz. `data/example_dataset.csv`). "
             "`human` ve `ai` gibi kaynağı belgelenmiş etiketler kullanın; yazarlık asla tahmin edilmez.")
    upload = st.file_uploader("Veri seti CSV", type=["csv"], key="dataset")
    if upload is None or not st.button("Veri setini değerlendir"):
        return
    if upload.size > 5_000_000:
        st.error("CSV en fazla 5 MB olabilir")
        return
    try:
        result = evaluate_dataset(load_dataset(upload.getvalue()))
    except ValueError as exc:
        st.error(str(exc))
        return
    for warning in result["warnings"]:
        st.warning(warning)
    _label_summary(result)
    for pair in result["comparisons"]:
        st.markdown(f"**{pair['a']} ve {pair['b']}** (n = {pair['n_a']} / {pair['n_b']})")
        st.dataframe(_comparison_rows(pair), hide_index=True)
        st.caption(f"Sözdizimi hata oranı farkı (Fisher exact p): {pair['syntax_error_fisher_p']}")
    st.download_button("Değerlendirme JSON'unu indir", json.dumps(result, indent=2, ensure_ascii=False),
                       file_name="evaluation.json")


def _evaluation_tab() -> None:
    st.write("Her kuralın kesinlik (precision) ve duyarlılık (recall) değeri, "
             "`quality_studio/ground_truth.py` içindeki elle etiketli kod parçalarına göre ölçülür.")
    if not st.button("Kural değerlendirmesini çalıştır"):
        return
    report = evaluate()
    micro = report["micro"]
    cols = st.columns(4)
    cols[0].metric("Vaka", report["cases"])
    cols[1].metric("Kesinlik (precision)", micro["precision"])
    cols[2].metric("Duyarlılık (recall)", micro["recall"])
    cols[3].metric("F1", micro["f1"])
    st.caption(f"%95 GA: kesinlik {micro['precision_ci95']}, duyarlılık {micro['recall_ci95']}. " + report["caveat"])
    st.dataframe([{"kural": rule, **row} for rule, row in report["rules"].items()], hide_index=True)
    if report["mismatches"]:
        st.markdown("**Etiketler ile motor arasındaki farklar**")
        st.dataframe(report["mismatches"], hide_index=True)


BADGE_COLOR = {"ok": "green", "warn": "orange", "fail": "red", "na": "gray"}
BADGE_MARK = {"ok": "✓", "warn": "!", "fail": "✗", "na": "–"}
DEFAULT_CASES = '[{"etiket": "tipik", "args": [10], "kwargs": {}}]'
SPINNER_TEXT = "Çalışıyor (Docker modunda ilk çalıştırmada imaj indirilebilir)..."


def _hybrid_intro() -> None:
    st.subheader("Hibrit İnceleme: statik + LLM + dinamik, tek ekranda")
    st.markdown("Zincir: **LLM önerir → AST doğrular → çalıştırma ölçer.** Her bulgu kartında hangi katmanın "
                "neyi doğruladığı rozetle gösterilir; en altta üç katman tek bir karara bağlanır.")
    with st.expander("Rozetler ne anlama gelir?"):
        st.markdown(
            "- **Statik:** AST kuralı bulguyu kodu çalıştırmadan buldu. Düzeltmeden sonra kod yeniden analiz edilir; "
            "bulgu artık yoksa *çözüldü* sayılır.\n"
            "- **LLM:** bu koda özgü açıklama üretti. Yalnızca motorun gerçekten bulduğu (kural, satır) çiftleri kabul edilir.\n"
            "- **Dinamik:** iki sürüm aynı girdilerle çalıştırıldı. Bir bulgunun *giderildiğini* kanıtlamaz; "
            "**davranışın korunduğunu** ve **hızın değiştiğini** gösterir, yalnızca denenen girdiler için.\n"
            "- ✓ doğrulandı · ! dikkat · ✗ reddedildi · – yapılmadı ya da yapılamadı")


def _badge_lines(badges: list[dict]) -> None:
    for badge in badges:
        color, mark = BADGE_COLOR[badge["status"]], BADGE_MARK[badge["status"]]
        st.markdown(f":{color}[**{badge['layer']} {mark}**] {badge['text']}")


def _hybrid_card(card: dict) -> None:
    f = card["finding"]
    title = f"{hybrid.card_marker(card)} {_severity_tr(f['severity']).upper()} · {f['rule']} · S{f['line']} · {f['message']}"
    with st.expander(title):
        cwe = f" · {f['cwe']}" if f.get("cwe") else ""
        st.write(f"**{_category_tr(f['category'])}** · güven: {CONFIDENCE_TR.get(f['confidence'], f['confidence'])}{cwe}")
        st.write("**Neden sorun?**", f["why"])
        st.write("**Nasıl düzelir?**", f["fix"])
        if card["note"]:
            st.info(f"**LLM açıklaması (bu koda özgü)**\n\n{card['note']['neden']}\n\n**Öneri:** {card['note']['duzeltme']}")
        _badge_lines(card["badges"])


def _hybrid_cards(cards: list[dict]) -> None:
    if not cards:
        st.success("Açık kurallardan bulgu çıkmadı. Bu, kodun güvenli veya doğru olduğunu garanti etmez.")
        return
    categories = sorted({c["finding"]["category"] for c in cards})
    left, right = st.columns(2)
    chosen_categories = left.multiselect("Kategoriler", categories, default=categories, format_func=_category_tr)
    chosen_severities = right.multiselect("Önem", SEVERITIES, default=SEVERITIES, format_func=_severity_tr)
    st.caption("İşaretler: ✓ düzeltmede çözüldü · • açık · ✗ dinamik ölçüm düzeltmeyi reddetti.")
    for card in cards:
        if card["finding"]["category"] in chosen_categories and card["finding"]["severity"] in chosen_severities:
            _hybrid_card(card)


def _hybrid_analysis():
    """Adım 1: kod girişi ve statik analiz. (kod, sonuç) döndürür; sonuç yoksa None."""
    st.markdown("#### 1) Kod")
    code = _load_editor()
    live = st.toggle("Canlı analiz (düzenledikçe yeniden analiz et)", value=True, key="live_analysis")
    stale = "snapshot" in st.session_state and st.session_state["snapshot"] != code
    if (st.button("Kodu analiz et", type="primary") or (live and stale)) and not _run_analysis(code):
        return code, None
    result = st.session_state.get("result")
    if result is None:
        st.info("Başlamak için **Kodu analiz et** düğmesine basın.")
        return code, None
    snapshot = st.session_state["snapshot"]
    if snapshot != code:
        st.warning("Editör son analizden sonra değişti; yenilemek için tekrar analiz edin.")
    if not result.valid:
        st.error("Geçersiz Python sözdizimi: " + result.findings[0].why)
        return snapshot, None
    return snapshot, result


def _hybrid_run(code: str, findings: list[dict], settings: llm.Settings, live: bool) -> bool:
    """Tek LLM çağrısı; başarılıysa Sonra sürümünü ve test girdilerini düzenleme kutularına yazar."""
    if not _llm_run("hy_review", hybrid.review_and_fix, code, findings, settings, live):
        return False
    data = st.session_state["hy_review"]["data"]
    if data.get("kod"):
        st.session_state["hy_after"] = data["kod"]
        st.session_state["hy_after_for"] = code
    if data["test_girdileri"]:
        st.session_state["hy_cases"] = hybrid.cases_to_text(data["test_girdileri"])
    return True


def _hybrid_review_notes(review: dict) -> None:
    _llm_badge(review)
    data = review["data"]
    if data["reddedilen"] or data["reddedilen_test"]:
        st.caption(f"Doğrulamadan geçemeyen {data['reddedilen']} açıklama ve {data['reddedilen_test']} test girdisi "
                   "atıldı (modelin uydurduğu kural/satır ya da geçersiz JSON).")
    if data["kod"] is None:
        st.warning("Model bu kez düzeltilmiş kod döndürmedi; açıklamalar yine de bulgu kartlarında görünür.")


def _quick_patches(snapshot: str) -> None:
    with st.expander("Kural tabanlı hızlı yamalar (LLM'siz, üç basit dönüşüm)"):
        patched = _patch_panel(snapshot, st.session_state["patches"])
        if patched is not None:
            st.session_state["hy_after"] = patched
            st.session_state["hy_after_for"] = snapshot
            st.rerun()


def _hybrid_llm_step(snapshot: str, findings: list[dict], settings: llm.Settings, live: bool, review) -> bool:
    """Adım 3: LLM düğmesi. Düğme kullanılabilir durumdaysa True döndürür."""
    st.markdown("#### 3) LLM ile düzelt")
    st.caption("Tek çağrı: bulgu açıklamaları + düzeltilmiş kod + test girdisi önerisi (1 istek). "
               "Anahtarsız da statik ve dinamik katmanlar çalışır.")
    has_key = bool(settings.resolved_key())
    if not has_key:
        st.info("LLM için API anahtarınızı sayfanın üstündeki **Bağlantı ayarları** bölümüne yapıştırın.")
    consent = st.checkbox("Bu kodun seçili sağlayıcıya gönderileceğini anlıyorum", key="llm_consent")
    ready = has_key and consent
    if st.button("Hibrit incelemeyi çalıştır (LLM açıklar, düzeltir, test girdisi önerir)", key="hy_run",
                 disabled=not ready) and _hybrid_run(snapshot, findings, settings, live):
        st.rerun()
    if review:
        _hybrid_review_notes(review)
    _quick_patches(snapshot)
    return ready


def _check_table(check: dict) -> None:
    st.table(pd.DataFrame([{"Kontrol": c["kontrol"], "Sonuç": "Geçti" if c["gecti"] else "Kaldı", "Ayrıntı": c["ayrinti"]}
                           for c in check["checks"]]).set_index("Kontrol"))


def _hybrid_compare(snapshot: str, after: str, check: dict | None, review) -> None:
    st.markdown("#### 4) Önce | Sonra (statik doğrulama)")
    st.session_state.setdefault("hy_after", "")
    left, right = st.columns(2)
    left.caption(f"Önce · {len(analyze(snapshot).findings)} bulgu")
    left.code(snapshot, language="python")
    right.text_area("Sonra sürümü (düzenleyebilirsiniz; düzenleyince her şey yeniden hesaplanır)", key="hy_after",
                    height=260)
    if check is None:
        st.info("Henüz Sonra sürümü yok. LLM düzeltmesi isteyin, hızlı yama uygulayın ya da sağ kutuya elle yazın.")
        return
    stale_for = st.session_state.get("hy_after_for")
    if stale_for is not None and stale_for != snapshot:
        st.warning("Sonra sürümü, editördeki şu anki koddan farklı bir kod için hazırlanmıştı. Yeniden üretin.")
    (st.success if check["verified"] else st.error)(
        "Statik doğrulama geçti." if check["verified"] else "Statik doğrulama başarısız: bu öneriye güvenmeyin.")
    _check_table(check)
    if check["effect"]:
        _impact_metrics(check["effect"])
    edited = bool(review) and review["data"].get("kod") is not None and review["data"]["kod"].strip() != after.strip()
    if review and review["data"]["degisiklikler"]:
        note = "elle düzenlediğiniz için artık geçerli olmayabilir" if edited else "LLM'in anlattığı değişiklikler"
        st.markdown(f"**{note}:** " + "; ".join(review["data"]["degisiklikler"]))
    _hybrid_compare_details(snapshot, after, check)


def _hybrid_compare_details(snapshot: str, after: str, check: dict) -> None:
    if not check["effect"]:
        return
    with st.expander("Yan yana fark, yapısal metrikler ve indirmeler"):
        lines = max(len(snapshot.splitlines()), len(after.splitlines()))
        components.html(render_side_by_side(snapshot, after, analyze(snapshot).issues, analyze(after).issues),
                        height=min(700, max(240, 110 + lines * 23)), scrolling=True)
        _impact_tables(check["effect"])
        _impact_downloads(snapshot, after, check["effect"])
    st.session_state["after_edit"] = after


def _hybrid_dynamic(snapshot: str, after: str) -> dict | None:
    """Dinamik katmanın şu anki durumu: ölçülemez nedeni, güncel ölçüm ya da None."""
    if not after.strip():
        return None
    info = hybrid.measurability(snapshot, after)
    if info["reason"]:
        return {"reason": info["reason"]}
    stored = st.session_state.get("hy_bench")
    if stored and stored["sources"][:2] == (snapshot, after):
        return stored
    return None


def _case_rows(result: dict) -> list[dict]:
    rows = []
    for case in result.get("cases", []):
        row = {"Girdi": case["label"], "Çıktı aynı mı?": "Evet" if case["same"] else "HAYIR"}
        if case.get("timed"):
            b, a = case["before"]["median_ms"], case["after"]["median_ms"]
            row.update({"Önce (ms)": round(b, 4), "Sonra (ms)": round(a, 4), "Hız oranı": f"{b / a:.2f}×" if a else "–"})
        elif not case["same"]:
            row.update({"Önce çıktısı": case["before_outcome"][:70], "Sonra çıktısı": case["after_outcome"][:70]})
        rows.append(row)
    return rows


def _hybrid_bench_result(stored: dict) -> None:
    result = stored["result"]
    mode_text = "Docker (izole)" if stored["mode"] == "docker" else "Yerel yedek (izole değil)"
    if result.get("cases"):
        st.dataframe(_case_rows(result), hide_index=True)
    if not result.get("ok"):
        st.error(result.get("error", "Ölçüm başarısız"))
        return
    summary = summarize(result)
    st.caption(f"Çalışma modu: {mode_text}. Süre ve bellek, Önce sürümünün en yavaş kaldığı girdi "
               f"({result.get('headline_case', '–')}) için raporlanır. Süre ve bellek ayrı turlarda ölçülür; "
               f"tracemalloc Python ayırmalarını sayar. Süre: {summary['time_verdict']} · "
               f"bellek: {summary['memory_verdict']}. (%10'dan küçük farklar gürültü sayılır.)")


def _hybrid_mode_and_consent() -> tuple[str, bool]:
    status = docker_status()
    mode_label = st.selectbox("Çalışma modu", [MODE_DOCKER, MODE_LOCAL], index=0 if status["daemon"] else 1,
                              key="hy_mode")
    mode = "docker" if mode_label == MODE_DOCKER else "yerel"
    if mode == "docker":
        text = "Kodun Docker içinde çalıştırılacağını anlıyorum"
    else:
        st.warning("Yerel yedek mod kodu bu bilgisayarda **izole olmadan** çalıştırır. Docker kurulu değilse bu "
                   "yedek kullanılır; yalnızca kendi yazdığınız ya da tamamen güvendiğiniz kodla kullanın. "
                   "LLM'in ürettiği kod da dışarıdan gelen kod sayılır.")
        text = "Kodun bu bilgisayarda izole olmadan çalışacağını ve güvendiğim bir kod olduğunu anlıyorum"
    return mode, st.checkbox(text, key="hy_consent")


def _hybrid_measure(snapshot: str, after: str, review) -> None:
    st.markdown("#### 5) Çalıştırarak ölç (dinamik)")
    if not after.strip():
        st.info("Ölçmek için önce bir Sonra sürümü gerekir.")
        return
    info = hybrid.measurability(snapshot, after)
    if info["reason"]:
        st.warning("Dinamik ölçüm yapılamıyor: " + info["reason"])
        return
    suggested = (review or {}).get("data", {}).get("fonksiyon")
    functions = info["functions"]
    function = st.selectbox("Ölçülecek fonksiyon", functions, index=functions.index(suggested) if suggested in functions else 0)
    st.session_state.setdefault("hy_cases", DEFAULT_CASES)
    text = st.text_area("Test girdileri (JSON). LLM önerdiyse burada görünür; düzenleyebilirsiniz", key="hy_cases",
                        height=200)
    st.caption('Her kayıt: {"etiket": ..., "args": [...], "kwargs": {...}}. Doğru çıktıyı bilmeniz gerekmez: '
               "Önce sürümü referanstır. Yalnızca bu girdiler için karşılaştırılır.")
    cases, error = hybrid.parse_cases(text)
    mode, consent = _hybrid_mode_and_consent()
    repeats = st.slider("Tekrar sayısı", 3, 40, 10, key="hy_repeats")
    if error:
        st.error(error)
    if st.button("Önerilen düzeltmeyi ölçerek doğrula", key="hy_bench_run", disabled=not consent or bool(error)):
        with st.spinner(SPINNER_TEXT):
            result = benchmark(snapshot, after, function, cases[0]["args"], cases[0]["kwargs"],
                               BenchmarkOptions(repeats=repeats, mode=mode), cases=cases)
        st.session_state["hy_bench"] = {"result": result, "sources": (snapshot, after, function), "mode": mode}
        st.rerun()
    stored = st.session_state.get("hy_bench")
    if stored and stored["sources"][:2] == (snapshot, after):
        _hybrid_bench_result(stored)
    elif stored:
        st.warning("Kod ya da Sonra sürümü değişti; önceki ölçüm geçersiz. Yeniden ölçün.")


def _hybrid_verdict(code: str, after: str, cards: list[dict], check: dict | None, dyn: dict | None) -> None:
    st.markdown("#### 6) Sonuç kartı")
    allow_change = st.checkbox("Bu düzeltmenin çıktıyı değiştirmesi beklenir (ör. md5 yerine sha256 gibi güvenlik düzeltmeleri)",
                               key="hy_allow_change")
    verdict = hybrid.overall_verdict(check, dyn, allow_change)
    box = {"kabul": st.success, "kabul_uyari": st.warning, "kismen": st.info, "bekliyor": st.info,
           "red": st.error}[verdict["decision"]]
    box(verdict["title"])
    st.table(pd.DataFrame([{"Katman": r["layer"], "Durum": BADGE_MARK[r["status"]], "Sonuç": r["text"]}
                           for r in verdict["rows"]]).set_index("Katman"))
    st.caption("Kabul, yalnızca denenen girdiler için geçerlidir; kodun her durumda aynı işi yaptığının kanıtı değildir. "
               "Güvenlik ya da kalite iyileşmesi statik kurallara dayanan bir göstergedir.")
    review = st.session_state.get("hy_review") or {}
    meta = {"source": review.get("source"), "model": review.get("model")}
    report = hybrid.hybrid_report(code, after or None, cards, check, dyn, meta)
    st.download_button("Hibrit raporu indir (JSON)", json.dumps(report, indent=2, ensure_ascii=False),
                       file_name="hybrid_report.json")


def _hybrid_dynamic_runner(snapshot: str):
    """Daha önce elle ölçüm yapıldıysa aynı işlev, girdi ve mod ile her aday için ölçen bir işlev döndürür; yoksa None."""
    stored = st.session_state.get("hy_bench")
    cases, error = hybrid.parse_cases(st.session_state.get("hy_cases", ""))
    if not stored or error or not cases:
        return None
    function, mode = stored["sources"][2], stored["mode"]
    repeats = st.session_state.get("hy_repeats", 10)

    def run(after: str) -> dict | None:
        if hybrid.measurability(snapshot, after)["reason"] or function not in hybrid.common_functions(snapshot, after):
            return None
        result = benchmark(snapshot, after, function, cases[0]["args"], cases[0]["kwargs"],
                           BenchmarkOptions(repeats=repeats, mode=mode), cases=cases)
        return {"result": result, "sources": (snapshot, after, function), "mode": mode}
    return run


def _hybrid_refine(snapshot: str, after: str, findings: list[dict], settings: llm.Settings, ready: bool,
                   check: dict | None) -> None:
    st.markdown("#### 7) Reddedildiyse: LLM'e geri bildirim ver, yeniden düzelttir")
    st.caption("Doğrulamanın bulduğu somut nedenler (kalan bulgular, farklı çıktılar, yavaşlık) LLM'e geri verilir; "
               "yeni öneri yine statik (ve ölçüm yapıldıysa dinamik) olarak doğrulanır. Her tur 1 LLM isteği harcar.")
    if not after.strip() or check is None:
        st.info("Önce bir Sonra sürümü gerekir.")
        return
    runner = _hybrid_dynamic_runner(snapshot)
    st.caption("Dinamik doğrulama: " + ("her turda aynı girdilerle yeniden ölçülür." if runner else
               "kapalı (önce 5. adımda bir kez ölçerseniz döngü her adayı da çalıştırarak doğrular)."))
    rounds = st.slider("En çok kaç tur denensin?", 1, hybrid.MAX_REFINE_ROUNDS, hybrid.MAX_REFINE_ROUNDS, key="hy_rounds")
    if not st.button("Kabul edilene kadar geri bildirimle düzelttir", key="hy_refine_btn", disabled=not ready):
        if not ready:
            st.caption("API anahtarı ve gönderim onayı gerekir (3. adım).")
        return
    with st.spinner("LLM geri bildirimle düzeltiyor ve doğrulanıyor..."):
        outcome = hybrid.auto_refine(snapshot, after, findings, settings, run_dynamic=runner, rounds=rounds,
                                     allow_behavior_change=st.session_state.get("hy_allow_change", False))
    if outcome["dyn"] and outcome["dyn"].get("sources"):
        st.session_state["hy_bench"] = outcome["dyn"]
    st.session_state["hy_refine_history"] = outcome["history"]
    if outcome["kod"] != after:
        st.session_state["hy_pending_after"] = (outcome["kod"], snapshot)
    st.rerun()


def _hybrid_refine_history() -> None:
    history = st.session_state.get("hy_refine_history")
    if not history:
        return
    with st.expander("Son düzelttirme denemesinin geçmişi", expanded=True):
        st.table(pd.DataFrame([{"Tur": h["tur"], "Karar": h["baslik"],
                                "LLM'e söylenen nedenler": " | ".join(h["nedenler"]) or "—",
                                "Hata": h.get("hata", "")} for h in history]).set_index("Tur"))


def _hybrid_second_opinion(snapshot: str, findings: list[dict], settings: llm.Settings, live: bool, ready: bool) -> None:
    with st.expander("LLM ikinci görüş: kuralların kaçırmış olabileceği adaylar (ana akışın dışında)"):
        if ready:
            _llm_candidates_section(snapshot, findings, settings, live)
        else:
            st.caption("Bu bölüm için API anahtarı ve yukarıdaki gönderim onayı gerekir. Adaylar kesin bulgu sayılmaz.")


def _hybrid_tab() -> None:
    pending = st.session_state.pop("hy_pending_after", None)  # widget çizilmeden önce yazılabilir
    if pending is not None:
        st.session_state["hy_after"], st.session_state["hy_after_for"] = pending
    for key in ("hy_after", "hy_cases"):  # Streamlit, çizilmeyen bir turda widget durumunu siler; burada korunur
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
    _hybrid_intro()
    settings, live = _llm_connection()
    snapshot, result = _hybrid_analysis()
    if result is None:
        return
    findings = result.issues
    review = _llm_fresh("hy_review", snapshot)
    st.session_state.setdefault("hy_after", "")
    after = st.session_state["hy_after"]
    check = llm.verify_fix(snapshot, after) if after.strip() else None
    dyn = _hybrid_dynamic(snapshot, after)
    after_findings = hybrid.after_findings_of(after) if check else None
    cards = hybrid.build_cards(findings, review["data"]["aciklamalar"] if review else None, after_findings, dyn,
                               llm_ran=review is not None)
    st.markdown("#### 2) Statik sonuç ve bulgu kartları")
    _summary_metrics(result)
    _code_viewer(snapshot, findings)
    _hybrid_cards(cards)
    ready = _hybrid_llm_step(snapshot, findings, settings, live, review)
    _hybrid_compare(snapshot, after, check, review)
    _hybrid_measure(snapshot, after, review)
    _hybrid_verdict(snapshot, after, cards, check, dyn)
    _hybrid_refine(snapshot, after, findings, settings, ready, check)
    _hybrid_refine_history()
    _hybrid_second_opinion(snapshot, findings, settings, live, ready)


def _guide_tab() -> None:
    st.subheader("Katmanlar ve sunum rehberi")
    names = ["Sunum rehberi", "Statik katman", "Dinamik katman (elle ölçüm)", "LLM katmanı"]
    for tab, render in zip(st.tabs(names), (_guide_text, _static_tab, _dynamic_tab, _llm_intro)):
        with tab:
            render()


def _guide_text() -> None:
    st.markdown("#### Üç katman, tek zincir")
    st.table(pd.DataFrame([
        {"Katman": "Statik (AST)", "Rolü": "Bulur ve doğrular", "Kanıt gücü": "Gösterge: kural tabanlı, kodu çalıştırmaz"},
        {"Katman": "LLM", "Rolü": "Anlar, açıklar, düzeltme ve test girdisi önerir",
         "Kanıt gücü": "Kanıt değil: her çıktısı doğrulanır"},
        {"Katman": "Dinamik (çalıştırma)", "Rolü": "Davranışı ve hızı ölçer", "Kanıt gücü": "Ölçüm: yalnızca denenen girdiler için"},
    ]).set_index("Katman"))
    st.markdown(
        "**Anlatım cümlesi:** *LLM önerir → AST doğrular → çalıştırma ölçer.* Bir öneri ancak statik doğrulamadan "
        "geçer ve denenen girdilerde çıktısı Önce sürümüyle aynı kalırsa **kabul edilebilir** sayılır.")
    with st.expander("Slayttaki maddeler araçta nerede?", expanded=True):
        st.markdown(
            "- **Hibrit inceleme motoru:** *Hibrit İnceleme* sekmesi (AST + LLM + çalıştırma, tek ekranda).\n"
            "- **Sorun sınıflandırması:** her bulgunun kategorisi (güvenlik, performans, kod kokusu, stil...) kartlarda ve grafikte.\n"
            "- **Açıklanmış öneriler:** her bulguda kuralın neden/nasıl metni, LLM'den gelen bu koda özgü açıklama.\n"
            "- **Karşılaştırmalı değerlendirme:** *Proje ve Değerlendirme* sekmesi (İnsan vs YZ, kural kesinliği/duyarlılığı).")
    with st.expander("Hocaya doğru ve yanlış söylemek"):
        st.markdown(
            "- Doğru: \"LLM düzeltmeyi önerdi; AST bulgunun gittiğini ve yeni bulgu çıkmadığını doğruladı; "
            "çalıştırma denenen girdilerde çıktının aynı kaldığını ve hızı ölçtü.\"\n"
            "- Yanlış: \"Araç kodu otomatik düzeltti ve doğruluğu kanıtladı.\" Düzeltmeyi LLM önerir; kanıt yalnızca "
            "denenen girdiler içindir.\n"
            "- Dinamik ölçüm bir bulgunun *giderildiğini* kanıtlamaz (ör. SQL enjeksiyonu); davranışın korunduğunu "
            "ve hızı gösterir.\n"
            "- Fonksiyonu olmayan düz script ölçülemez; araç bunu açıkça söyler.")
    with st.expander("Önerilen canlı gösterim akışı"):
        st.markdown(
            "1. Hazır örnek kodla **Kodu analiz et**: statik bulgular ve rozetler.\n"
            "2. **Hibrit incelemeyi çalıştır**: LLM açıklar, düzeltir, test girdisi önerir (kota: 1 istek).\n"
            "3. **Önce | Sonra** statik doğrulamasını göster; Sonra kutusunu düzenleyip her şeyin yeniden hesaplandığını göster.\n"
            "4. **Ölçerek doğrula**: girdi başına çıktı aynı mı, hız oranı.\n"
            "5. **Sonuç kartı**: üç katmanın tek kararı. İnternet kesilirse son başarılı yanıt *kayıtlı yanıt* uyarısıyla gelir.")


def _evaluation_group() -> None:
    names = ["Proje (ZIP)", "İnsan vs YZ", "Kural Değerlendirmesi"]
    for tab, render in zip(st.tabs(names), (_project_tab, _dataset_tab, _evaluation_tab)):
        with tab:
            render()


_header()
TAB_NAMES = ["Hibrit İnceleme", "Proje ve Değerlendirme", "Katmanlar ve Sunum Rehberi"]
RENDERERS = (_hybrid_tab, _evaluation_group, _guide_tab)
for tab, render in zip(st.tabs(TAB_NAMES), RENDERERS):
    with tab:
        render()
