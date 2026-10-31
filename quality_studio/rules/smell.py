"""Code smell, style and reliability rules (CQ1xx, CQ2xx)."""
from __future__ import annotations

import ast

from ..astutil import SCOPE_NODES
from ..metrics import function_complexity
from .context import Context

_NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)
_MUTABLE_CALLS = {"list", "dict", "set", "bytearray"}
_EXEMPT_DECORATORS = ("setter", "getter", "deleter", "overload")
_EXCEPTION_NAMES = {"Exception", "builtins.Exception"}
_PRINT_NAMES = {"print", "builtins.print"}


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_bool(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def _decorator_names(func) -> list[str]:
    names = []
    for decorator in func.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.append(target.attr)
        elif isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _catches_exception(ctx: Context, handler: ast.ExceptHandler) -> bool:
    """True for 'except Exception' (also inside a tuple such as 'except (ValueError, Exception)')."""
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(t is not None and ctx.dotted(t) in _EXCEPTION_NAMES for t in caught)


def _only_prints(ctx: Context, body: list[ast.stmt]) -> bool:
    """True when every statement of the block is a plain print(...) call."""
    return bool(body) and all(
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
        and ctx.dotted(stmt.value.func) in _PRINT_NAMES
        for stmt in body
    )


def _unbreakable(line: str) -> bool:
    """A line that is one long token (a URL or path), optionally behind '#', cannot be wrapped."""
    chunks = line.split()
    return len(chunks) == 1 or (len(chunks) == 2 and chunks[0] == "#")


def _long_lines(ctx: Context) -> None:
    """CQ109: report every line longer than config.max_line_length."""
    limit = ctx.config.max_line_length
    for number, line in enumerate(ctx.source.split("\n"), start=1):
        text = line.rstrip("\r")
        if len(text) > limit and not _unbreakable(text):
            ctx.report("CQ109", ctx.tree, f"Satır çok uzun ({len(text)} karakter, sınır {limit})", line=number)


def _is_elif(parent: ast.AST, child: ast.AST) -> bool:
    return isinstance(parent, ast.If) and isinstance(child, ast.If) and parent.orelse == [child]


def _max_nesting(scope: ast.AST) -> tuple[int, ast.AST | None]:
    """Deepest nesting of control-flow statements and the first node that reaches it.

    An 'elif' is a sibling branch, not a deeper level, so it does not add nesting.
    """
    best, where = 0, None

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal best, where
        for child in ast.iter_child_nodes(node):
            if isinstance(child, SCOPE_NODES):
                continue
            adds = isinstance(child, _NESTING_NODES) and not _is_elif(node, child)
            level = depth + 1 if adds else depth
            if level > best:
                best, where = level, child
            walk(child, level)

    walk(scope, 0)
    return best, where


class _Visitor(ast.NodeVisitor):
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.function_depth = 0

    def _visit_function(self, node) -> None:
        cfg = self.ctx.config
        report = self.ctx.report
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        if length > cfg.max_function_lines:
            report("CQ101", node, f"Long function ({length} lines)")
        params = [a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                  if a.arg not in {"self", "cls"}]
        if len(params) > cfg.max_parameters:
            report("CQ102", node, f"Too many parameters ({len(params)})")
        complexity = function_complexity(node)
        if complexity > cfg.max_complexity:
            severity = "high" if complexity > 2 * cfg.max_complexity else "medium"
            report("CQ107", node, f"High cyclomatic complexity ({complexity})", severity=severity)
        depth, deepest = _max_nesting(node)
        if depth > cfg.max_nesting and deepest is not None:
            report("CQ108", deepest, f"Control flow nested {depth} levels deep")
        self._docstring(node)
        self._mutable_defaults(node)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _docstring(self, node) -> None:
        exempt = (
            node.name.startswith(("_", "test", "visit_"))
            or self.function_depth > 0
            or any(d in _EXEMPT_DECORATORS for d in _decorator_names(node))
        )
        if not exempt and not ast.get_docstring(node):
            self.ctx.report("CQ103", node)

    def _mutable_defaults(self, node) -> None:
        defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
        for default in defaults:
            literal = isinstance(default, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp))
            call = (isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
                    and default.func.id in _MUTABLE_CALLS)
            if literal or call:
                self.ctx.report("CQ105", default)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.ctx.report("CQ201", node)
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self.ctx.report("CQ202", node)
        if _catches_exception(self.ctx, node) and _only_prints(self.ctx, node.body):
            self.ctx.report("CQ110", node, "except Exception bloğunda yalnızca print var")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        pairs = list(zip(node.ops, zip(operands, operands[1:])))
        for op, (left, right) in pairs:
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            if _is_none(left) or _is_none(right):
                self.ctx.report("CQ104", node, "Comparison to None using ==/!=")
                break
            if _is_bool(left) or _is_bool(right):
                self.ctx.report("CQ106", node)
                break
        self.generic_visit(node)


def run(ctx: Context) -> None:
    """Run the code smell, style and reliability checks."""
    _Visitor(ctx).visit(ctx.tree)
    _long_lines(ctx)


