"""Code Quality Studio: single Streamlit interface (no external services, no API keys).

Run with:  streamlit run app.py
Submitted code is parsed, never executed (except in the opt-in Docker benchmark).
"""
from __future__ import annotations

import ast
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from quality_studio.benchmark import BenchmarkOptions, benchmark
from quality_studio.codeview import render_code
from quality_studio.compare import evaluate_dataset, load_dataset
from quality_studio.diffview import render_side_by_side
from quality_studio.engine import analyze
from quality_studio.evaluation import evaluate
from quality_studio.impact import impact, patch_effects
from quality_studio.project_scan import analyzed, project_summary, scan_archive
from quality_studio.refactor import apply, suggest, unified_diff
from quality_studio.report import to_html, to_json, to_sarif

MAX_SOURCE_BYTES = 250_000
SEVERITIES = ["critical", "high", "medium", "low", "info"]
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
st.title("◈ Code Quality Studio")
st.caption("Static analysis of Python source: security, performance, code smells, redundancy and "
           "AI-generation indicators. The score and indicators are heuristics, not proof.")


def _read_upload(upload, limit: int = MAX_SOURCE_BYTES) -> str | None:
    if upload.size > limit:
        st.error(f"File exceeds {limit // 1000} KB")
        return None
    try:
        return upload.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        st.error("File must be UTF-8")
        return None


def _findings_table(findings: list[dict]) -> None:
    if not findings:
        st.success("No findings from the enabled rules. This does not guarantee safe or correct code.")
        return
    categories = sorted({f["category"] for f in findings})
    left, right = st.columns(2)
    chosen_categories = left.multiselect("Categories", categories, default=categories)
    chosen_severities = right.multiselect("Severity", SEVERITIES, default=SEVERITIES)
    for f in findings:
        if f["category"] not in chosen_categories or f["severity"] not in chosen_severities:
            continue
        with st.expander(f"{f['severity'].upper()} · {f['rule']} · L{f['line']} · {f['message']}"):
            cwe = f" · {f['cwe']}" if f.get("cwe") else ""
            st.write(f"**{f['category']}** · confidence: {f['confidence']}{cwe}")
            st.write("**Why:**", f["why"])
            st.write("**Suggested action:**", f["fix"])
            if f.get("corroborated_by"):
                st.caption("Confirmed by: " + ", ".join(f["corroborated_by"]))


def _code_viewer(code: str, findings: list[dict]) -> None:
    lines = sorted({f["line"] for f in findings})
    focus = st.selectbox("Jump to a finding", [None, *lines],
                         format_func=lambda n: "All lines" if n is None else f"Line {n}")
    height = min(650, max(220, 100 + len(code.splitlines()) * 23))
    components.html(render_code(code, findings, focus_line=focus), height=height, scrolling=True)


def _benchmark_panel(before: str, after: str) -> None:
    def top_level(code: str) -> set[str]:
        return {n.name for n in ast.parse(code).body if isinstance(n, ast.FunctionDef)}

    common = sorted(top_level(before) & top_level(after))
    if not common:
        st.info("Both versions need a top-level function with the same name to enable benchmarking.")
        return
    st.warning("Runs the code inside a restricted Docker container (no network, read-only, limited CPU/RAM), "
               "never on the Streamlit host. Docker is not a perfect security boundary: use trusted code only.")
    function = st.selectbox("Function to benchmark", common, key="bench_fn")
    args_text = st.text_input("Positional arguments (JSON array)", value="[]", key="bench_args")
    kwargs_text = st.text_input("Keyword arguments (JSON object)", value="{}", key="bench_kwargs")
    repeats = st.slider("Repetitions", 3, 40, 15, key="bench_repeats")
    consent = st.checkbox("I understand this executes the code inside Docker", key="bench_consent")
    if st.button("Run isolated benchmark", disabled=not consent, key="bench_run"):
        try:
            args, kwargs = json.loads(args_text), json.loads(kwargs_text)
        except ValueError as exc:
            st.error(f"Invalid JSON: {exc}")
            return
        with st.spinner("Running (the first run may download the Docker image)..."):
            st.session_state["bench_result"] = benchmark(before, after, function, args, kwargs,
                                                         BenchmarkOptions(repeats=repeats))
    result = st.session_state.get("bench_result")
    if not result:
        return
    if not result.get("ok"):
        st.error(result.get("error", "Benchmark failed"))
        return
    b, a = result["before"], result["after"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Before (median)", f"{b['median_ms']:.4f} ms")
    c2.metric("After (median)", f"{a['median_ms']:.4f} ms")
    c3.metric("Speed ratio", f"{b['median_ms'] / a['median_ms']:.2f}×" if a["median_ms"] else "n/a")
    m1, m2 = st.columns(2)
    m1.metric("Before peak allocations", f"{b['median_peak_bytes'] / 1024:.1f} KiB")
    m2.metric("After peak allocations", f"{a['median_peak_bytes'] / 1024:.1f} KiB")
    st.caption("Outputs matched for this input only. Time and memory were measured in separate rounds. "
               "tracemalloc counts Python allocations, not process RSS.")
    st.download_button("Download benchmark JSON", json.dumps(result, indent=2), file_name="benchmark.json")


def _impact_panel(before: str, after: str) -> None:
    effect = impact(before, after)
    if not effect["valid"]:
        st.error("The After version has invalid syntax; correct it to compare.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quality score", f"{effect['score_before']} → {effect['score_after']}", delta=effect["score_delta"])
    c2.metric("Findings", f"{effect['findings_before']} → {effect['findings_after']}",
              delta=effect["findings_after"] - effect["findings_before"], delta_color="inverse")
    c3.metric("Resolved", effect["resolved"])
    c4.metric("New issues", effect["introduced"], delta=effect["introduced"], delta_color="inverse")
    labels = {"nonblank_lines": "Nonblank lines", "branches": "Branches", "loops": "Loops",
              "functions": "Functions", "complexity_total": "Complexity (total)", "ast_nodes": "AST nodes"}
    frame = pd.DataFrame([{"Metric": label, "Before": effect["before"][key], "After": effect["after"][key]}
                          for key, label in labels.items()]).set_index("Metric")
    st.bar_chart(frame)
    st.caption("Static counts only. Fewer lines or nodes do not necessarily mean faster or better code.")
    for key, title in (("resolved_rules", "Resolved"), ("introduced_rules", "New")):
        if effect[key]:
            st.markdown(f"**{title} findings**")
            st.dataframe(effect[key], hide_index=True)
    st.markdown("**Before → After**")
    lines = max(len(before.splitlines()), len(after.splitlines()))
    components.html(render_side_by_side(before, after, analyze(before).issues, analyze(after).issues),
                    height=min(700, max(240, 110 + lines * 23)), scrolling=True)
    with st.expander("Unified diff"):
        st.code(unified_diff(before, after) or "No changes", language="diff")
    st.download_button("Download improved .py", after, file_name="improved.py", mime="text/x-python")
    st.download_button("Download HTML report", to_html((before, analyze(before)), (after, analyze(after))),
                       file_name="quality_report.html", mime="text/html")
    st.download_button("Download impact JSON", json.dumps(effect, indent=2), file_name="impact.json")
    with st.expander("Optional: measure real speed with Docker"):
        _benchmark_panel(before, after)


def _effect_label(effect: dict | None) -> str:
    if effect is None:
        return ""
    return f" · score {effect['score_before']} → {effect['score_after']} ({effect['score_delta']:+d})"


def _patch_panel(source: str, patches: list[dict]) -> str | None:
    if not patches:
        st.write("No conservative automatic patches available. Other findings need manual review.")
        return None
    st.caption("Patches are syntax-checked but NOT behavior-verified. Read each caution. "
               "The score shown next to each patch is its static effect alone.")
    effects = patch_effects(source, patches)
    chosen = []
    for index, patch in enumerate(patches):
        label = f"L{patch['line']} · {patch['rule']} · {patch['description']}{_effect_label(effects[index])}"
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
            effect = impact(source, preview)
            if effect["valid"]:
                c1, c2, c3 = st.columns(3)
                c1.metric("Score if applied", f"{effect['score_before']} → {effect['score_after']}",
                          delta=effect["score_delta"])
                c2.metric("Findings resolved", effect["resolved"])
                c3.metric("New issues", effect["introduced"], delta=effect["introduced"], delta_color="inverse")
    if not st.button("Apply selected fixes", disabled=preview is None):
        return None
    return preview


def _review_tab() -> None:
    upload = st.file_uploader("Upload a .py file (optional)", type=["py"], key="source_upload")
    if upload is not None:
        signature = (upload.name, upload.size)
        if st.session_state.get("upload_signature") != signature:
            text = _read_upload(upload)
            if text is not None:
                st.session_state["editor"] = text
                st.session_state["upload_signature"] = signature
    st.session_state.setdefault("editor", EXAMPLE)
    code = st.text_area("Python source", key="editor", height=280)
    live = st.toggle("Live analysis (re-analyze as you edit)", value=True, key="live_analysis")
    stale = "snapshot" in st.session_state and st.session_state["snapshot"] != code
    if st.button("Analyze source", type="primary") or (live and stale):
        try:
            result = analyze(code)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state.update(snapshot=code, result=result, patches=suggest(code) if result.valid else [])
        st.session_state.pop("after", None)
    result = st.session_state.get("result")
    if result is None:
        st.info("Select **Analyze source** to start.")
        return
    snapshot = st.session_state["snapshot"]
    if snapshot != code:
        st.warning("The editor changed since the last analysis; analyze again to refresh.")
    if not result.valid:
        st.error("Invalid Python syntax: " + result.findings[0].why)
        return
    metrics, findings = result.metrics, result.issues
    cols = st.columns(5)
    cols[0].metric("Findings", len(findings))
    cols[1].metric("Heuristic score", metrics["score"])
    cols[2].metric("Complexity (max fn)", metrics["complexity_max_function"])
    cols[3].metric("AI indicators / 100 lines", metrics["ai_indicators_per_100_lines"])
    cols[4].metric("Suppressed (noqa)", result.suppressed)
    st.caption("AI indicators are weak probabilistic signals (e.g. restating comments, redundant temporaries). "
               "They never prove authorship and do not affect the score.")
    _code_viewer(snapshot, findings)
    _findings_table(findings)
    st.subheader("Fixes")
    patched = _patch_panel(snapshot, st.session_state["patches"])
    if patched is not None:
        st.session_state["after"] = patched
    if "after" in st.session_state:
        st.subheader("Proposed code")
        after = st.text_area("Edit the After version", value=st.session_state["after"], height=220)
        _impact_panel(snapshot, after)


def _project_tab() -> None:
    st.write("Upload a ZIP of a Python project. Files are analysed statically; "
             "`__pycache__`, virtualenvs and `.git` are skipped.")
    archive = st.file_uploader("Project ZIP", type=["zip"], key="project_zip")
    if archive is None or not st.button("Scan project"):
        return
    try:
        files = scan_archive(archive.getvalue())
    except ValueError as exc:
        st.error(str(exc))
        return
    summary = project_summary(files)
    cols = st.columns(4)
    cols[0].metric("Files analysed", f"{summary['analyzed_files']}/{summary['files']}")
    cols[1].metric("Findings", summary["issues"])
    cols[2].metric("High priority", summary["high_priority"])
    cols[3].metric("Syntax-error files", summary["syntax_error_files"])
    st.dataframe([{k: v for k, v in f.items() if k in ("file", "line", "rule", "severity", "category", "message")}
                  for f in summary["findings"]], hide_index=True)
    entries = analyzed(files)
    st.download_button("Download SARIF", to_sarif(entries), file_name="results.sarif.json")
    st.download_button("Download JSON", to_json(entries), file_name="project_review.json")


def _dataset_tab() -> None:
    st.write("CSV with columns `label,code`, one sample per row (see `data/example_dataset.csv`). "
             "Use documented provenance labels such as `human` and `ai`; authorship is never inferred.")
    upload = st.file_uploader("Dataset CSV", type=["csv"], key="dataset")
    if upload is None or not st.button("Evaluate dataset"):
        return
    if upload.size > 5_000_000:
        st.error("Maximum CSV size is 5 MB")
        return
    try:
        result = evaluate_dataset(load_dataset(upload.getvalue()))
    except ValueError as exc:
        st.error(str(exc))
        return
    for warning in result["warnings"]:
        st.warning(warning)
    st.markdown("**Per-label summary**")
    st.dataframe([{"label": k, **{n: v for n, v in s.items() if n != "category_counts"}}
                  for k, s in result["labels"].items()], hide_index=True)
    for pair in result["comparisons"]:
        st.markdown(f"**{pair['a']} vs {pair['b']}** (n = {pair['n_a']} / {pair['n_b']})")
        rows = []
        for key, name in (("defect_density", "Defect density"), ("ai_indicator_density", "AI-indicator density")):
            stat = pair[key]
            if stat["computable"]:
                rows.append({"metric": name, "mean diff (a-b)": stat["mean_difference"], "95% CI": str(stat["ci95"]),
                             "Mann-Whitney p": stat["p_value"], "Bonferroni p": stat["p_adjusted_bonferroni"],
                             "Cliff's delta": stat["cliffs_delta"], "effect": stat["effect_size"]})
            else:
                rows.append({"metric": name, "mean diff (a-b)": stat["reason"]})
        st.dataframe(rows, hide_index=True)
        st.caption(f"Syntax-error rate difference (Fisher exact p): {pair['syntax_error_fisher_p']}")
    st.download_button("Download evaluation JSON", json.dumps(result, indent=2), file_name="evaluation.json")


def _evaluation_tab() -> None:
    st.write("Precision and recall of every rule against the hand-labeled snippets in "
             "`quality_studio/ground_truth.py`.")
    if not st.button("Run rule evaluation"):
        return
    report = evaluate()
    micro = report["micro"]
    cols = st.columns(4)
    cols[0].metric("Cases", report["cases"])
    cols[1].metric("Precision", micro["precision"])
    cols[2].metric("Recall", micro["recall"])
    cols[3].metric("F1", micro["f1"])
    st.caption(f"95% CI: precision {micro['precision_ci95']}, recall {micro['recall_ci95']}. " + report["caveat"])
    st.dataframe([{"rule": rule, **row} for rule, row in report["rules"].items()], hide_index=True)
    if report["mismatches"]:
        st.markdown("**Disagreements between labels and engine**")
        st.dataframe(report["mismatches"], hide_index=True)


tabs = st.tabs(["Code Review", "Project (ZIP)", "Dataset Comparison", "Rule Evaluation"])
for tab, render in zip(tabs, (_review_tab, _project_tab, _dataset_tab, _evaluation_tab)):
    with tab:
        render()
