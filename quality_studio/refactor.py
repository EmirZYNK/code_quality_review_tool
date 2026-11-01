"""Conservative, opt-in source patches. Never evaluates submitted code."""
from __future__ import annotations

import ast
import difflib
import re
from dataclasses import asdict, dataclass

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


@dataclass
class Patch:
    rule: str
    line: int
    before: str
    after: str
    description: str
    caution: str
    start: int
    end: int


class _Offsets:
    """Convert ast (line, UTF-8 byte column) positions into str indices.

    ast reports col_offset in UTF-8 bytes, not characters, so non-ASCII text earlier on
    the line shifts every position. Line breaks follow ast: \\n, \\r\\n and \\r only.
    """

    def __init__(self, source: str):
        self.source = source
        self.starts = [0]
        for match in _LINE_BREAK.finditer(source):
            self.starts.append(match.end())

    def index(self, lineno: int, byte_col: int) -> int:
        """str index for an ast (line, byte column) position."""
        line_start = self.starts[lineno - 1]
        line_end = self.starts[lineno] if lineno < len(self.starts) else len(self.source)
        raw = self.source[line_start:line_end].encode("utf-8")
        return line_start + len(raw[:byte_col].decode("utf-8", errors="ignore"))


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _yaml_alias(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name for a in node.names if a.name == "yaml")
    return names


def _none_comparison(source: str, offsets: _Offsets, node: ast.Compare) -> Patch | None:
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return None
    left, right = node.left, node.comparators[0]
    if not (_is_none(left) or _is_none(right)):
        return None
    left_text = ast.get_source_segment(source, left)
    right_text = ast.get_source_segment(source, right)
    if left_text is None or right_text is None:
        return None
    operator = "is not" if isinstance(node.ops[0], ast.NotEq) else "is"
    start = offsets.index(node.lineno, node.col_offset)
    end = offsets.index(node.end_lineno, node.end_col_offset)
    old = source[start:end]
    replacement = f"{left_text} {operator} {right_text}"
    if old == replacement:
        return None
    return Patch("CQ104", node.lineno, old, replacement, "None için kimlik karşılaştırması kullan",
                 "Davranış değişir: özel bir __eq__ kasıtlı olarak kullanılıyorsa.", start, end)


def _bare_except(source: str, offsets: _Offsets, node: ast.ExceptHandler) -> Patch | None:
    start = offsets.index(node.lineno, node.col_offset)
    keyword_end = start + len("except")
    if source[start:keyword_end] != "except" or not re.match(r"\s*:", source[keyword_end:keyword_end + 20]):
        return None
    return Patch("CQ201", node.lineno, "", " Exception", "BaseException yerine Exception yakala",
                 "Davranış değişir: KeyboardInterrupt ve SystemExit artık yakalanmaz.",
                 keyword_end, keyword_end)


def _yaml_load(source: str, offsets: _Offsets, node: ast.Call, yaml_names: set[str]) -> Patch | None:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "load"
            and isinstance(func.value, ast.Name) and func.value.id in yaml_names):
        return None
    if len(node.args) != 1 or node.keywords:
        return None
    start = offsets.index(func.lineno, func.col_offset)
    end = offsets.index(func.end_lineno, func.end_col_offset)
    old = source[start:end]
    return Patch("SEC107", node.lineno, old, f"{func.value.id}.safe_load", "yaml.safe_load kullan",
                 "safe_load özel Python etiketlerini reddeder; onlara dayanan belgeler yüklenemez.",
                 start, end)


def suggest(source: str) -> list[dict]:
    """Offer targeted transformations with clearly disclosed behavior caveats."""
    tree = ast.parse(source)
    offsets = _Offsets(source)
    yaml_names = _yaml_alias(tree)
    patches: list[Patch | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            patches.append(_none_comparison(source, offsets, node))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            patches.append(_bare_except(source, offsets, node))
        elif isinstance(node, ast.Call):
            patches.append(_yaml_load(source, offsets, node, yaml_names))
    found = sorted((p for p in patches if p is not None), key=lambda p: (p.start, p.end))
    return [asdict(p) for p in found]


def apply(source: str, patches: list[dict], selected) -> str:
    """Apply the chosen patches; rejects overlaps and stale patches, re-parses the result."""
    chosen = [p for i, p in enumerate(patches) if i in set(selected)]
    last_end = -1
    for patch in chosen:
        if patch["start"] < last_end:
            raise ValueError("Yamalar çakışıyor")
        if source[patch["start"]:patch["end"]] != patch["before"]:
            raise ValueError("Yama eskimiş; yeniden analiz edin")
        last_end = patch["end"]
    result = source
    for patch in reversed(chosen):
        result = result[:patch["start"]] + patch["after"] + result[patch["end"]:]
    ast.parse(result)
    return result


def unified_diff(before: str, after: str) -> str:
    """Unified diff between two source texts."""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="before.py", tofile="after.py"))
