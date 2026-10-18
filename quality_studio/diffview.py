"""Side-by-side Before/After code view with resolved / new finding markers (standalone HTML).

Static analysis only: nothing is executed. Findings are matched between the two versions by
(rule, category, severity, message), the same fingerprint `impact.py` uses, so the numbers shown
here agree with the impact metrics.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from html import escape

_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#dbeafe;font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}
.toolbar{position:sticky;top:0;z-index:2;display:flex;gap:18px;align-items:center;padding:10px 16px;background:#111d31;
color:#94a3b8;font:12px system-ui;border-bottom:1px solid #243149}
.toolbar label{cursor:pointer;color:#dbeafe}.toolbar .ok{color:#4ade80}.toolbar .bad{color:#fb7185}
table{width:100%;border-collapse:collapse;table-layout:fixed}
td{vertical-align:top;white-space:pre-wrap;word-break:break-word;padding:0 10px;min-height:23px}
td.num{width:44px;text-align:right;color:#64748b;user-select:none;border-right:1px solid #243149;padding-right:10px}
td.src{width:calc(50% - 44px)}
tr.gap td{background:#0f172a}
tr.changed td.l{background:rgba(251,113,133,.10)}tr.changed td.r{background:rgba(74,222,128,.10)}
tr.changed{animation:flash .9s ease-out}
@keyframes flash{from{filter:brightness(1.8)}to{filter:brightness(1)}}
td.r.num,td.l.num{border-left:1px solid #243149}
.chip{display:inline-block;margin-left:10px;padding:0 7px;border-radius:9px;font:11px/18px system-ui;white-space:nowrap}
.chip.resolved{background:rgba(74,222,128,.18);color:#4ade80}
.chip.new{background:rgba(251,113,133,.20);color:#fb7185}
.chip.kept{background:rgba(148,163,184,.16);color:#94a3b8}
body.only tr.same{display:none}
"""

_SCRIPT = """
document.getElementById('only').addEventListener('change',function(e){
  document.body.classList.toggle('only', e.target.checked);
});
"""


def _fingerprint(issue: dict) -> tuple:
    return (issue.get("rule"), issue.get("category"), issue.get("severity"), issue.get("message"))


def classify(before_issues, after_issues) -> tuple[set[int], set[int]]:
    """Return (resolved indexes into before_issues, introduced indexes into after_issues).

    Findings with the same fingerprint are paired in order; unpaired ones in the Before list are
    resolved, unpaired ones in the After list are new.
    """
    before_groups: dict[tuple, list[int]] = defaultdict(list)
    after_groups: dict[tuple, list[int]] = defaultdict(list)
    for i, issue in enumerate(before_issues):
        before_groups[_fingerprint(issue)].append(i)
    for i, issue in enumerate(after_issues):
        after_groups[_fingerprint(issue)].append(i)
    resolved: set[int] = set()
    introduced: set[int] = set()
    for key in before_groups.keys() | after_groups.keys():
        b, a = before_groups.get(key, []), after_groups.get(key, [])
        paired = min(len(b), len(a))
        resolved.update(b[paired:])
        introduced.update(a[paired:])
    return resolved, introduced


def _line_chips(issues: list[dict], kinds: dict[int, str], line: int) -> str:
    chips = []
    for index, issue in enumerate(issues):
        if issue.get("line") != line:
            continue
        kind = kinds.get(index, "kept")
        label = {"resolved": "✓ resolved", "new": "+ new", "kept": ""}[kind]
        text = f"{label} {issue.get('rule', '')}".strip()
        tip = escape(str(issue.get("message", "")), quote=True)
        chips.append(f'<span class="chip {kind}" title="{tip}">{escape(text)}</span>')
    return "".join(chips)


def _rows(before_lines: list[str], after_lines: list[str]):
    """Yield (left_no, left_text, right_no, right_text, changed) aligned rows."""
    matcher = SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                yield i1 + k + 1, before_lines[i1 + k], j1 + k + 1, after_lines[j1 + k], False
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            left = i1 + k if i1 + k < i2 else None
            right = j1 + k if j1 + k < j2 else None
            yield (left + 1 if left is not None else None, before_lines[left] if left is not None else "",
                   right + 1 if right is not None else None, after_lines[right] if right is not None else "", True)


def render_side_by_side(before: str, after: str, before_issues=(), after_issues=(), max_lines: int = 1500) -> str:
    """Two-column HTML diff. `*_issues` are finding dicts (line, rule, severity, category, message)."""
    before_issues, after_issues = list(before_issues), list(after_issues)
    resolved, introduced = classify(before_issues, after_issues)
    before_kinds = {i: "resolved" for i in resolved}
    after_kinds = {i: "new" for i in introduced}
    before_lines = before.splitlines() or [""]
    after_lines = after.splitlines() or [""]
    rows = []
    shown = 0
    for left_no, left, right_no, right, changed in _rows(before_lines, after_lines):
        if shown >= max_lines:
            break
        shown += 1
        left_chips = _line_chips(before_issues, before_kinds, left_no) if left_no else ""
        right_chips = _line_chips(after_issues, after_kinds, right_no) if right_no else ""
        classes = "changed" if changed else "same"
        rows.append(
            f'<tr class="{classes}">'
            f'<td class="num l">{left_no or ""}</td><td class="src l">{escape(left)}{left_chips}</td>'
            f'<td class="num r">{right_no or ""}</td><td class="src r">{escape(right)}{right_chips}</td></tr>')
    total = len(before_lines) + len(after_lines)
    notice = (f'<div style="padding:10px;color:#fbbf24">Preview limited to {max_lines:,} rows.</div>'
              if total and shown >= max_lines else "")
    toolbar = (f'<div class="toolbar"><span>BEFORE → AFTER</span>'
               f'<span class="ok">{len(resolved)} resolved</span><span class="bad">{len(introduced)} new</span>'
               f'<label><input type="checkbox" id="only"> only changed lines</label></div>')
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_STYLE}</style></head><body>'
            f'{toolbar}<table>{"".join(rows)}</table>{notice}<script>{_SCRIPT}</script></body></html>')
