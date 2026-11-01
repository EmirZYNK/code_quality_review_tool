"""Redundant-code rules (RED0xx): unused names, duplicates, dead code."""
from __future__ import annotations

import ast

from ..astutil import FUNCTION_NODES, own_nodes, statement_lists
from .context import Context

_TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _annotation_nodes(tree: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            found.append(node.annotation)
        elif isinstance(node, FUNCTION_NODES) and node.returns is not None:
            found.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
    return found


def _names_in_string(text: str) -> set[str]:
    try:
        parsed = ast.parse(text, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(parsed) if isinstance(n, ast.Name)}


def _annotation_names(tree: ast.AST) -> set[str]:
    """Names referenced from string annotations such as 'Foo' or 'list[Foo]'."""
    strings = [sub.value for annotation in _annotation_nodes(tree) for sub in ast.walk(annotation)
               if isinstance(sub, ast.Constant) and isinstance(sub.value, str)]
    return set().union(*map(_names_in_string, strings)) if strings else set()


def _exported_names(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return {e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()


def _catches_import_error(node: ast.Try) -> bool:
    return any(
        h.type is None or "ImportError" in ast.dump(h.type) or "ModuleNotFoundError" in ast.dump(h.type)
        for h in node.handlers
    )


def _guarded_by_import_error(tree: ast.Module) -> set[int]:
    """Line numbers of imports inside try/except ImportError (availability probes)."""
    guarded_bodies = [n.body for n in ast.walk(tree) if isinstance(n, ast.Try) and _catches_import_error(n)]
    nodes = (n for body in guarded_bodies for stmt in body for n in ast.walk(stmt))
    return {n.lineno for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))}


def _import_bindings(tree: ast.Module, guarded: set[int]):
    """Yield (import node, displayed name, bound name) for every checkable import."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.lineno in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        for alias in node.names:
            reexport = alias.asname is not None and alias.asname == alias.name  # import x as x
            if alias.name != "*" and not reexport:
                yield node, alias.asname or alias.name, alias.asname or alias.name.split(".")[0]


def _unused_imports(ctx: Context) -> None:
    used = {n.id for n in ast.walk(ctx.tree) if isinstance(n, ast.Name)}
    used |= _annotation_names(ctx.tree) | _exported_names(ctx.tree)
    for node, shown, bound in _import_bindings(ctx.tree, _guarded_by_import_error(ctx.tree)):
        if bound not in used:
            ctx.report("RED001", node, f"Kullanılmayan import '{shown}'")


def _uses_dynamic_scope(func: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"locals", "vars"}
               for n in ast.walk(func))


def _loaded_names(func: ast.AST) -> set[str]:
    loads = {n.id for n in ast.walk(func) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store)}
    loads |= {n.target.id for n in ast.walk(func) if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)}
    return loads


def _position(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _unused_in_function(ctx: Context, func: ast.AST) -> None:
    if _uses_dynamic_scope(func):
        return
    declared = {name for n in own_nodes(func) if isinstance(n, (ast.Global, ast.Nonlocal)) for name in n.names}
    ignored = declared | _loaded_names(func)
    stored = (pair for node in sorted(own_nodes(func), key=_position) for pair in _stored_names(node))
    reported: set[str] = set()
    for name, anchor in stored:
        if name.startswith("_") or name in ignored or name in reported:
            continue
        reported.add(name)
        ctx.report("RED002", anchor, f"Kullanılmayan yerel değişken '{name}'")


def _unused_variables(ctx: Context) -> None:
    for func in (n for n in ast.walk(ctx.tree) if isinstance(n, FUNCTION_NODES)):
        _unused_in_function(ctx, func)


def _stored_names(node: ast.AST):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                yield target.id, node
    elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
        yield node.target.id, node
    elif isinstance(node, ast.ExceptHandler) and node.name:
        yield node.name, node


def _body_without_docstring(func) -> list[ast.stmt]:
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _node_count(nodes) -> int:
    return sum(1 for node in nodes for _ in ast.walk(node))


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= r_end and end >= r_start for r_start, r_end in ranges)


def _duplicate_functions(ctx: Context) -> list[tuple[int, int]]:
    seen: dict[str, ast.AST] = {}
    reported: list[tuple[int, int]] = []
    functions = sorted((n for n in ast.walk(ctx.tree) if isinstance(n, FUNCTION_NODES)), key=lambda n: n.lineno)
    for func in functions:
        body = _body_without_docstring(func)
        if _node_count(body) < ctx.config.duplicate_min_nodes:
            continue
        key = ast.dump(func.args) + "|" + "|".join(ast.dump(s) for s in body)
        original = seen.get(key)
        if original is None:
            seen[key] = func
            continue
        ctx.report("RED004", func, f"'{func.name}' fonksiyonunun gövdesi '{original.name}' ile aynı (satır {original.lineno})")
        reported.append((func.lineno, func.end_lineno or func.lineno))
    return reported


def _window_occurrences(ctx: Context) -> dict[tuple[str, ...], list[tuple[int, int]]]:
    size = ctx.config.duplicate_min_statements
    occurrences: dict[tuple[str, ...], list[tuple[int, int]]] = {}
    for block in statement_lists(ctx.tree):
        windows = (block[i:i + size] for i in range(len(block) - size + 1))
        for window in windows:
            if _node_count(window) < ctx.config.duplicate_min_nodes:
                continue
            key = tuple(ast.dump(s) for s in window)
            span = (window[0].lineno, window[-1].end_lineno or window[-1].lineno)
            occurrences.setdefault(key, []).append(span)
    return occurrences


def _duplicate_blocks(ctx: Context, skip: list[tuple[int, int]]) -> None:
    repeated = sorted(sorted(set(v)) for v in _window_occurrences(ctx).values() if len(set(v)) > 1)
    claimed = list(skip)
    for spans in repeated:
        first = spans[0]
        if _overlaps(*first, claimed):
            continue
        claimed.append(first)
        for span in (sp for sp in spans[1:] if not _overlaps(*sp, claimed)):
            claimed.append(span)
            ctx.report("RED003", ctx.tree, f"Tekrarlanan blok (satır {first[0]}-{first[1]} ile aynı)", line=span[0])


def _dead_code_and_pass(ctx: Context) -> None:
    for block in statement_lists(ctx.tree):
        for index, stmt in enumerate(block[:-1]):
            if isinstance(stmt, _TERMINATORS):
                ctx.report("RED005", block[index + 1], "Erişilemeyen kod")
                break
        if len(block) > 1:
            for stmt in block:
                if isinstance(stmt, ast.Pass):
                    ctx.report("RED006", stmt)


def run(ctx: Context) -> None:
    """Run all redundancy checks."""
    _unused_imports(ctx)
    _unused_variables(ctx)
    duplicate_ranges = _duplicate_functions(ctx)
    _duplicate_blocks(ctx, duplicate_ranges)
    _dead_code_and_pass(ctx)
