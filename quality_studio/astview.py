"""Statik analizin görünür kılınması: AST ağacı, hangi bulgunun hangi düğümde çıktığı ve özetler.

Hiçbir kod çalıştırılmaz; yalnızca `ast` ile ayrıştırma yapılır.
"""
from __future__ import annotations

import ast
from collections import Counter

from .models import CATEGORY_TR
from .rules import RULES

# En sık görülen düğüm türlerinin kısa Türkçe karşılıkları.
NODE_TR = {
    "Module": "modül (dosyanın kökü)",
    "FunctionDef": "fonksiyon tanımı",
    "AsyncFunctionDef": "async fonksiyon tanımı",
    "ClassDef": "sınıf tanımı",
    "Return": "return ifadesi",
    "Assign": "atama",
    "AugAssign": "işlemli atama (+=)",
    "AnnAssign": "tür belirtmeli atama",
    "Expr": "ifade satırı",
    "Call": "fonksiyon çağrısı",
    "Attribute": "nokta ile erişim",
    "Name": "değişken adı",
    "Constant": "sabit değer",
    "BinOp": "ikili işlem",
    "BoolOp": "and/or işlemi",
    "Compare": "karşılaştırma",
    "If": "if bloğu",
    "For": "for döngüsü",
    "While": "while döngüsü",
    "Try": "try/except bloğu",
    "ExceptHandler": "except kolu",
    "Import": "import",
    "ImportFrom": "from ... import",
    "Pass": "pass",
    "With": "with bloğu",
    "Assert": "assert",
    "JoinedStr": "f-string",
    "ListComp": "liste üretimi",
    "arguments": "parametre listesi",
    "arg": "parametre",
}


def node_tr(name: str) -> str:
    """Düğüm türünün kısa Türkçe açıklaması (bilinmiyorsa türün kendi adı)."""
    return NODE_TR.get(name, name)


def _label(node: ast.AST) -> str:
    kind = type(node).__name__
    for attr in ("name", "id", "attr", "arg"):
        value = getattr(node, attr, None)
        if isinstance(value, str):
            return f"{kind} '{value}'"
    if isinstance(node, ast.Constant):
        text = repr(node.value)
        return f"{kind} {text if len(text) <= 24 else text[:21] + '...'}"
    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
        return f"{kind} ({type(node.op).__name__})"
    return kind


def _position(node: ast.AST) -> tuple[int, int] | None:
    line = getattr(node, "lineno", None)
    if line is None:
        return None
    return line, getattr(node, "end_lineno", None) or line


def _flag_map(findings: list[dict]) -> dict[tuple[int, int], list[str]]:
    flags: dict[tuple[int, int], list[str]] = {}
    for item in findings:
        rules = flags.setdefault((item["line"], item["end_line"]), [])
        if item["rule"] not in rules:
            rules.append(item["rule"])
    return flags


def tree_text(code: str, findings: list[dict], *, only_flagged: bool = False, max_rows: int = 400) -> str:
    """AST ağacını girintili metin olarak verir; bulgu çıkan düğümler '⚠ KURAL' ile işaretlenir."""
    tree = ast.parse(code)
    flags = _flag_map(findings)
    used: set[tuple[int, int]] = set()

    # Aynı konumu paylaşan düğümlerden (ör. Expr ve Call) yalnızca ilkini işaretle.
    marks: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        position = _position(node)
        if position in flags and position not in used:
            marks[id(node)] = flags[position]
            used.add(position)

    def has_mark(node: ast.AST) -> bool:
        return id(node) in marks or any(has_mark(child) for child in ast.iter_child_nodes(node))

    rows: list[str] = []

    def walk(node: ast.AST, depth: int) -> None:
        if len(rows) >= max_rows:
            return
        if only_flagged and not has_mark(node):
            return
        position = _position(node)
        where = f"  · satır {position[0]}" if position else ""
        mark = f"   ⚠ {', '.join(marks[id(node)])}" if id(node) in marks else ""
        rows.append(f"{'    ' * depth}{_label(node)}{where}{mark}")
        for child in ast.iter_child_nodes(node):
            walk(child, depth + 1)

    walk(tree, 0)
    if len(rows) >= max_rows:
        rows.append(f"... (ilk {max_rows} düğüm gösterildi)")
    return "\n".join(rows)


def finding_nodes(code: str, findings: list[dict]) -> list[dict]:
    """Her bulgu için: hangi satırda, hangi AST düğümlerinde ortaya çıktığı."""
    tree = ast.parse(code)
    by_position: dict[tuple[int, int], list[str]] = {}
    by_line: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        position = _position(node)
        if position is None:
            continue
        by_position.setdefault(position, []).append(type(node).__name__)
        by_line.setdefault(position[0], []).append(type(node).__name__)
    rows = []
    for item in findings:
        names = by_position.get((item["line"], item["end_line"])) or by_line.get(item["line"]) or ["Module"]
        unique = list(dict.fromkeys(names))[:3]
        rows.append({
            "Satır": item["line"],
            "Kural": item["rule"],
            "Kategori": CATEGORY_TR.get(item["category"], item["category"]),
            "AST düğümü": " → ".join(unique),
            "Düğüm açıklaması": node_tr(unique[0]),
            "Bulgu": item["message"],
        })
    return rows


def category_counts(findings: list[dict]) -> dict[str, int]:
    """Türkçe kategori adına göre bulgu sayıları."""
    counts = Counter(CATEGORY_TR.get(f["category"], f["category"]) for f in findings)
    return dict(counts.most_common())


def rule_table(findings: list[dict]) -> list[dict]:
    """Tüm kural kataloğu; bu kodda kaç kez tetiklendiği ile birlikte."""
    fired = Counter(f["rule"] for f in findings)
    rows = []
    for meta in RULES.values():
        rows.append({
            "Kural": meta.id,
            "Ad": meta.name,
            "Kategori": CATEGORY_TR.get(meta.category, meta.category),
            "Önem": meta.severity,
            "CWE": meta.cwe or "-",
            "Neden sorun?": meta.why,
            "Nasıl düzelir?": meta.fix,
            "Bu kodda": fired.get(meta.id, 0),
        })
    return rows
