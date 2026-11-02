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
.dim .source{opacity:.45}
.note{margin:2px 16px 6px 72px;padding:6px 10px;border-radius:6px;border-left:3px solid;font:12px/1.5 system-ui;
white-space:normal;max-width:900px}
.note b{display:block}
.note.onay{background:rgba(52,211,153,.10);border-color:#34d399}.note.eledi{background:rgba(148,163,184,.12);border-color:#64748b;color:#94a3b8}
.note.baglam{background:rgba(251,191,36,.10);border-color:#fbbf24}.note.aciklama{background:rgba(96,165,250,.10);border-color:#60a5fa}
.note.ek{background:rgba(167,139,250,.10);border-color:#a78bfa}
"""


def _background(index: int, focus_line: int | None, severity: str | None) -> str:
    if index == focus_line:
        return "rgba(96,165,250,.14)"
    return "rgba(251,113,133,.08)" if severity in ("critical", "high") else "transparent"


NOTE_KINDS = ("onay", "eledi", "baglam", "aciklama", "ek")


def _group_by_line(items) -> dict:
    grouped = defaultdict(list)
    for item in items:
        line = item.get("line")
        if isinstance(line, int) and line > 0:
            grouped[line].append(item)
    return grouped


def _line_rows(index: int, content: str, findings: list, notes: list, focus_line: int | None) -> list[str]:
    """Bir kod satırı ve altındaki LLM notları. Tüm notları 'eledi' olan bulgulu satır soluk ve gri gösterilir."""
    severity = max(findings, key=lambda f: RANK.get(f.get("severity"), 0))["severity"] if findings else None
    tooltip = escape(" | ".join(f"{f.get('rule', '')}: {f.get('message', '')}" for f in findings), quote=True)
    eliminated = bool(notes) and bool(findings) and all(n.get("kind") == "eledi" for n in notes)
    color = "#64748b" if eliminated else COLORS.get(severity, "transparent")
    background = "transparent" if eliminated and index != focus_line else _background(index, focus_line, severity)
    style = f"background:{background};border-left:3px solid {color}"
    rows = [f'<div class="line{" dim" if eliminated else ""}" style="{style}" title="{tooltip}">'
            f'<span class="num">{index}</span><span class="source">{escape(content)}</span></div>']
    for note in notes:
        kind = note.get("kind") if note.get("kind") in NOTE_KINDS else "aciklama"
        rows.append(f'<div class="note {kind}"><b>{escape(str(note.get("label", "")))}</b>'
                    f'{escape(str(note.get("text", "")))}</div>')
    return rows


def render_code(code: str, issues=(), focus_line: int | None = None, max_lines: int = 1500, annotations=()) -> str:
    """Render escaped source with a severity gutter and an optional focused line.

    `issues` are dicts with line/severity/rule/message. Nothing is executed.
    `annotations` (optional): dicts with line/kind/label/text shown as a note under the line; a line whose notes
    are all kind "eledi" (LLM eliminated the finding) is dimmed and gets a gray gutter.
    """
    by_line, notes_by_line = _group_by_line(issues), _group_by_line(annotations)
    lines = code.splitlines() or [""]
    truncated = len(lines) > max_lines
    lines = lines[:max_lines]
    rows: list[str] = []
    for index, content in enumerate(lines, 1):
        rows += _line_rows(index, content, by_line.get(index, []), notes_by_line.get(index, []), focus_line)
    notice = f'<div class="notice">Önizleme ilk {max_lines:,} satırla sınırlı.</div>' if truncated else ""
    total = sum(len(v) for v in by_line.values())
    note_count = sum(len(v) for v in notes_by_line.values())
    extra = f" · {note_count} LLM notu" if note_count else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_STYLE}</style></head><body>'
            f'<div class="toolbar">PYTHON · YALNIZCA OKUMA · {len(lines)} satır · {total} bulgu{extra}</div>'
            f'<div class="code">{"".join(rows)}</div>{notice}</body></html>')
