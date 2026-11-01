"""Probabilistic indicators that code may be machine-generated (AIP0xx).

These are weak signals, never proof of authorship. They are reported at severity 'info'
and are excluded from the quality score.
"""
from __future__ import annotations

import ast
import re

from ..astutil import FUNCTION_NODES, statement_lists
from .context import Context

_RESTATING = re.compile(
    r"^#\s*(initiali[sz]e|create|define|import|return|loop|iterate|check|call|increment|decrement|"
    r"set|get|add|append|print|calculate|compute|convert|update|store|assign|declare|instantiate)\b",
    re.IGNORECASE,
)
_BOILERPLATE_START = ("this function", "function to", "helper function", "a function that", "method to")
_BROAD = {"Exception", "BaseException"}
_LOG_PREFIXES = ("print", "logging.", "logger.", "log.")


def _temporary_returns(ctx: Context) -> None:
    for block in statement_lists(ctx.tree):
        for first, second in zip(block, block[1:]):
            if (isinstance(first, ast.Assign) and len(first.targets) == 1
                    and isinstance(first.targets[0], ast.Name)
                    and isinstance(second, ast.Return) and isinstance(second.value, ast.Name)
                    and second.value.id == first.targets[0].id):
                ctx.report("AIP001", first, f"Geçici değişken '{first.targets[0].id}' hemen döndürülüyor")


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    return handler.type is None or (isinstance(handler.type, ast.Name) and handler.type.id in _BROAD)


def _benign_statement(ctx: Context, stmt: ast.stmt) -> bool:
    """A statement that merely returns a literal default or logs/prints."""
    if isinstance(stmt, ast.Return):
        literal = (ast.Constant, ast.List, ast.Dict, ast.Tuple, ast.Set)
        return stmt.value is None or isinstance(stmt.value, literal)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return ctx.dotted(stmt.value.func).startswith(_LOG_PREFIXES)
    return False


def _swallowing_handler(ctx: Context, handler: ast.ExceptHandler) -> bool:
    """True when a broad handler neither re-raises nor does more than log/return a default."""
    if not _is_broad_handler(handler):
        return False
    if any(isinstance(n, ast.Raise) for s in handler.body for n in ast.walk(s)):
        return False
    if all(isinstance(s, ast.Pass) for s in handler.body):
        return False  # already covered by CQ202
    return all(_benign_statement(ctx, stmt) for stmt in handler.body)


def _defensive_try(ctx: Context) -> None:
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Try) and any(_swallowing_handler(ctx, h) for h in node.handlers):
            ctx.report("AIP002", node)


def _restating_comments(ctx: Context) -> None:
    hits = [c for c in ctx.comments
            if c.standalone and _RESTATING.match(c.text) and len(c.text.split()) <= 8]
    if len(hits) >= ctx.config.restating_comment_min:
        lines = ", ".join(str(c.line) for c in hits[:5])
        ctx.report("AIP003", ctx.tree, f"{len(hits)} yorum kodu tekrar ediyor (satırlar {lines})", line=hits[0].line)


def _boilerplate_docstrings(ctx: Context) -> None:
    for node in ast.walk(ctx.tree):
        if not isinstance(node, FUNCTION_NODES):
            continue
        doc = ast.get_docstring(node)
        if not doc:
            continue
        statements = [s for s in node.body[1:] if not isinstance(s, ast.Pass)]
        templated = "Args:" in doc and "Returns:" in doc and len(statements) <= 2
        if doc.strip().lower().startswith(_BOILERPLATE_START) or templated:
            ctx.report("AIP004", node)


def _verbose_names(ctx: Context) -> None:
    cfg = ctx.config
    seen: set[str] = set()

    def check(name: str, node: ast.AST) -> None:
        if name in seen:
            return
        words = [w for w in name.strip("_").split("_") if w]
        if len(words) >= cfg.verbose_name_words or len(name) >= cfg.verbose_name_chars:
            seen.add(name)
            ctx.report("AIP005", node, f"Gereksiz uzun isim '{name}'")

    for node in ast.walk(ctx.tree):
        if isinstance(node, FUNCTION_NODES):
            check(node.name, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    check(target.id, node)


def run(ctx: Context) -> None:
    """Run all AI-pattern indicator checks."""
    _temporary_returns(ctx)
    _defensive_try(ctx)
    _restating_comments(ctx)
    _boilerplate_docstrings(ctx)
    _verbose_names(ctx)
