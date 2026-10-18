"""Report writers: text, JSON, SARIF 2.1.0 and a standalone escaped HTML page."""
from __future__ import annotations

import json
from html import escape

from . import __version__
from .models import AnalysisResult
from .rules import RULES

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_SARIF_LEVEL = {"info": "note", "low": "note", "medium": "warning", "high": "error", "critical": "error"}


def to_json(entries: list[tuple[str, AnalysisResult]]) -> str:
    """JSON report for one or more analysed files."""
    payload = {
        "tool": "quality-studio",
        "version": __version__,
        "source_executed": False,
        "files": [{"path": path, **result.to_dict()} for path, result in entries],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def to_sarif(entries: list[tuple[str, AnalysisResult]]) -> str:
    """SARIF 2.1.0 report for CI code-scanning tools."""
    rule_ids = list(RULES)
    rules = [
        {
            "id": meta.id,
            "name": meta.name.replace(" ", ""),
            "shortDescription": {"text": meta.name},
            "fullDescription": {"text": meta.why},
            "help": {"text": meta.fix},
            "defaultConfiguration": {"level": _SARIF_LEVEL[meta.severity]},
            "properties": {
                "category": meta.category,
                "tags": [meta.category.lower().replace(" ", "-")] + ([f"external/cwe/{meta.cwe.lower()}"] if meta.cwe else []),
            },
        }
        for meta in RULES.values()
    ]
    results = []
    for path, result in entries:
        for finding in result.findings:
            item = {
                "ruleId": finding.rule,
                "level": _SARIF_LEVEL[finding.severity],
                "message": {"text": f"{finding.message}. {finding.why}"},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": path.replace("\\", "/")},
                    "region": {"startLine": finding.line, "endLine": finding.end_line},
                }}],
                "properties": {"confidence": finding.confidence, "category": finding.category,
                               "source": finding.source, "corroboratedBy": list(finding.corroborated_by)},
            }
            if finding.rule in rule_ids:
                item["ruleIndex"] = rule_ids.index(finding.rule)
            results.append(item)
    document = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "quality-studio", "version": __version__, "rules": rules}},
            "results": results,
        }],
    }
    return json.dumps(document, indent=2, ensure_ascii=False)


def to_text(entries: list[tuple[str, AnalysisResult]]) -> str:
    """One line per finding plus a summary line."""
    lines: list[str] = []
    for path, result in entries:
        for finding in result.findings:
            lines.append(f"{path}:{finding.line}: {finding.rule} [{finding.severity}] {finding.message}")
    total = sum(len(r.findings) for _, r in entries)
    lines.append(f"{total} finding(s) in {len(entries)} file(s).")
    return "\n".join(lines)


def _section(title: str, code: str, result: AnalysisResult) -> str:
    rows = "".join(
        f"<tr><td>{f.line}</td><td>{escape(f.rule)}</td><td>{escape(f.severity)}</td>"
        f"<td>{escape(f.category)}</td><td>{escape(f.message)}</td><td>{escape(f.fix)}</td></tr>"
        for f in result.findings
    )
    score = escape(str(result.metrics.get("score", "n/a")))
    return (f"<section><h2>{escape(title)}</h2><p>Heuristic score: {score} | Findings: {len(result.findings)}</p>"
            f"<pre>{escape(code)}</pre><table><thead><tr><th>Line</th><th>Rule</th><th>Severity</th>"
            f"<th>Category</th><th>Issue</th><th>Suggestion</th></tr></thead><tbody>{rows}</tbody></table></section>")


def to_html(before: tuple[str, AnalysisResult], after: tuple[str, AnalysisResult] | None = None) -> str:
    """Standalone, HTML-escaped report; pass `after` for a before/after view."""
    body = _section("Before" if after else "Source", *before)
    if after:
        body += _section("After", *after)
    style = ("body{font:15px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;background:#0d1117;color:#e6edf3}"
             "pre{overflow:auto;background:#161b22;padding:20px;border-radius:8px}"
             "table{border-collapse:collapse;width:100%;font-size:13px}"
             "th,td{border:1px solid #30363d;padding:9px;text-align:left}section{margin-bottom:36px}h1{color:#5eead4}")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width\"><title>Code Quality Report</title>"
            f"<style>{style}</style></head><body><h1>Code Quality Review</h1>"
            "<p>Static heuristics only. Submitted source was not executed. Scores do not prove correctness or security.</p>"
            f"{body}</body></html>")
