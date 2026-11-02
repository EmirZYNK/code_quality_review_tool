"""Read-only, line-numbered code view with severity gutters (standalone HTML)."""
from __future__ import annotations

from collections import defaultdict
from html import escape

COLORS = {"critical": "#ef4444", "high": "#fb7185", "medium": "#fbbf24", "low": "#60a5fa", "info": "#94a3b8"}
RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}

_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#dbeafe;font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}
.toolbar{position:sticky;top:0;padding:10px 16px;background:#111d31;color:#94a3b8;font:12px system-ui;border-bottom:1px solid #243149}
.code{overflow-x:auto;padding:12px 0}.line{display:flex;min-height:23px;white-space:pre}
.num{color:#64748b;text-align:right;min-width:56px;padding-right:16px;user-select:none;border-right:1px solid #243149;margin-right:16px}
.source{padding-right:20px}.notice{padding:10px;color:#fbbf24}
"""


def _background(index: int, focus_line: int | None, severity: str | None) -> str:
    if index == focus_line:
        return "rgba(96,165,250,.14)"
    return "rgba(251,113,133,.08)" if severity in ("critical", "high") else "transparent"


def render_code(code: str, issues=(), focus_line: int | None = None, max_lines: int = 1500) -> str:
    """Render escaped source with a severity gutter and an optional focused line.

    `issues` are dicts with line/severity/rule/message. Nothing is executed.
    """
    by_line = defaultdict(list)
    for issue in issues:
        line = issue.get("line")
        if isinstance(line, int) and line > 0:
            by_line[line].append(issue)
    lines = code.splitlines() or [""]
    truncated = len(lines) > max_lines
    lines = lines[:max_lines]
    rows = []
    for index, content in enumerate(lines, 1):
        findings = by_line.get(index, [])
        severity = max(findings, key=lambda f: RANK.get(f.get("severity"), 0))["severity"] if findings else None
        tooltip = escape(" | ".join(f"{f.get('rule', '')}: {f.get('message', '')}" for f in findings), quote=True)
        style = f"background:{_background(index, focus_line, severity)};border-left:3px solid {COLORS.get(severity, 'transparent')}"
        rows.append(f'<div class="line" style="{style}" title="{tooltip}">'
                    f'<span class="num">{index}</span><span class="source">{escape(content)}</span></div>')
    notice = f'<div class="notice">Önizleme ilk {max_lines:,} satırla sınırlı.</div>' if truncated else ""
    total = sum(len(v) for v in by_line.values())
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_STYLE}</style></head><body>'
            f'<div class="toolbar">PYTHON · YALNIZCA OKUMA · {len(lines)} satır · {total} bulgu</div>'
            f'<div class="code">{"".join(rows)}</div>{notice}</body></html>')
