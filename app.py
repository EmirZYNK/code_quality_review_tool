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
    llm_col.info("**LLM katmanı:** henüz bağlı değil (planlanan adım).")


def _read_upload(upload, limit: int = MAX_SOURCE_BYTES) -> str | None:
    if upload.size > limit:
        st.error(f"Dosya {limit // 1000} KB sınırını aşıyor")
        return None
    try:
        return upload.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        st.error("Dosya UTF-8 olmalı")
        return None


def _findings_table(findings: list[dict]) -> None:
    if not findings:
        st.success("Açık kurallardan bulgu çıkmadı. Bu, kodun güvenli veya doğru olduğunu garanti etmez.")
        return
    categories = sorted({f["category"] for f in findings})
    left, right = st.columns(2)
    chosen_categories = left.multiselect("Kategoriler", categories, default=categories, format_func=_category_tr)
    chosen_severities = right.multiselect("Önem", SEVERITIES, default=SEVERITIES, format_func=_severity_tr)
    for f in findings:
        if f["category"] not in chosen_categories or f["severity"] not in chosen_severities:
            continue
        title = f"{_severity_tr(f['severity']).upper()} · {f['rule']} · S{f['line']} · {f['message']}"
        with st.expander(title):
            _finding_details(f)


def _finding_details(f: dict) -> None:
    cwe = f" · {f['cwe']}" if f.get("cwe") else ""
    layer = LAYER_LABEL.get(f.get("source", "AST"), f.get("source", ""))
    st.write(f"**{_category_tr(f['category'])}** · güven: {CONFIDENCE_TR.get(f['confidence'], f['confidence'])}"
             f"{cwe} · katman: {layer}")
    st.write("**Neden sorun?**", f["why"])
    st.write("**Nasıl düzelir?**", f["fix"])
    if f.get("corroborated_by"):
        st.caption("Şunlarla doğrulandı: " + ", ".join(f["corroborated_by"]))


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


def _impact_panel(before: str, after: str) -> None:
    effect = impact(before, after)
    if not effect["valid"]:
        st.error("Sonra sürümünde sözdizimi hatası var; karşılaştırmak için düzeltin.")
        return
    _impact_metrics(effect)
    _impact_tables(effect)
    st.markdown("**Önce → Sonra**")
    lines = max(len(before.splitlines()), len(after.splitlines()))
    components.html(render_side_by_side(before, after, analyze(before).issues, analyze(after).issues),
                    height=min(700, max(240, 110 + lines * 23)), scrolling=True)
    _impact_downloads(before, after, effect)
    st.info("Gerçek hız ve bellek ölçümü için **Dinamik Analiz** sekmesine bakın.")


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


def _review_tab() -> None:
    code = _load_editor()
    live = st.toggle("Canlı analiz (düzenledikçe yeniden analiz et)", value=True, key="live_analysis")
    stale = "snapshot" in st.session_state and st.session_state["snapshot"] != code
    if (st.button("Kodu analiz et", type="primary") or (live and stale)) and not _run_analysis(code):
        return
    result = st.session_state.get("result")
    if result is None:
        st.info("Başlamak için **Kodu analiz et** düğmesine basın.")
        return
    snapshot = st.session_state["snapshot"]
    if snapshot != code:
        st.warning("Editör son analizden sonra değişti; yenilemek için tekrar analiz edin.")
    if not result.valid:
        st.error("Geçersiz Python sözdizimi: " + result.findings[0].why)
        return
    _summary_metrics(result)
    _code_viewer(snapshot, result.issues)
    _findings_table(result.issues)
    _fix_section(snapshot)


def _fix_section(snapshot: str) -> None:
    st.subheader("Düzeltmeler")
    patched = _patch_panel(snapshot, st.session_state["patches"])
    if patched is not None:
        st.session_state["after"] = patched
    if "after" in st.session_state:
        st.subheader("Önerilen kod")
        after = st.text_area("Sonra sürümünü düzenle", value=st.session_state["after"], height=220)
        st.session_state["after_edit"] = after
        _impact_panel(snapshot, after)


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


def _llm_tab() -> None:
    st.subheader("LLM Açıklama katmanı")
    st.info("Bu katman henüz bağlı değil. Planlanan çalışma şekli aşağıda özetlenmiştir.")
    st.markdown(
        "- **Görevi:** bulguları kısa Türkçe ile açıklamak ve statik analizin kaçırdığı adayları önermek.\n"
        "- **Güvence:** LLM'in söylediği her şey AST ile çapraz doğrulanır; doğrulanamayan öneri "
        "\"doğrulanmadı\" etiketiyle gösterilir.\n"
        "- **Gizlilik:** API anahtarı koda gömülmez, ortam değişkeninden okunur.")


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


_header()
TAB_NAMES = ["Kod İnceleme", "Statik Analiz", "Dinamik Analiz", "LLM Açıklama", "İnsan vs YZ", "Proje (ZIP)",
             "Kural Değerlendirmesi"]
RENDERERS = (_review_tab, _static_tab, _dynamic_tab, _llm_tab, _dataset_tab, _project_tab, _evaluation_tab)
for tab, render in zip(st.tabs(TAB_NAMES), RENDERERS):
    with tab:
        render()
