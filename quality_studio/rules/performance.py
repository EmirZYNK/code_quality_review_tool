"""Performance heuristics (PERF1xx). These are hints: real cost depends on data sizes."""
from __future__ import annotations

import ast

from ..astutil import SCOPE_NODES, is_str_const, own_nodes
from .context import Context

_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SEVERITY_BY_DEPTH = {2: ("low", "low"), 3: ("medium", "medium")}


def _small_iteration(node: ast.AST, limit: int) -> bool:
    """True for iteration over a short literal or range(<small constant>)."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return len(node.elts) <= limit and not any(isinstance(e, ast.Starred) for e in node.elts)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range"
            and node.args and not node.keywords
            and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in node.args)):
        try:
            return len(range(*(a.value for a in node.args))) <= limit
        except (TypeError, ValueError):
            return False
    return False


def _names(node: ast.AST) -> frozenset[str]:
    return frozenset(n.id for n in ast.walk(node) if isinstance(n, ast.Name))


def _adds_level(iterable: ast.AST, bound: frozenset[str], limit: int) -> bool:
    """A loop multiplies work only if its iterable is independent of the enclosing loop
    variables (so 'for cell in row' inside 'for row in rows' is linear overall) and not small."""
    return not _small_iteration(iterable, limit) and not (_names(iterable) & bound)


def _comprehension_levels(node: ast.AST, bound: frozenset[str], limit: int) -> tuple[int, frozenset[str]]:
    levels = 0
    for generator in node.generators:
        levels += _adds_level(generator.iter, bound, limit)
        bound = bound | _names(generator.target)
    return levels, bound


def loop_depth(node: ast.AST, limit: int, bound: frozenset[str] = frozenset()) -> int:
    """Deepest multiplicative loop nesting under node (nested scopes ignored)."""
    own, inner_bound = 0, bound
    if isinstance(node, (ast.For, ast.AsyncFor)):
        own = int(_adds_level(node.iter, bound, limit))
        inner_bound = bound | _names(node.target)
    elif isinstance(node, ast.While):
        own = 1
    elif isinstance(node, _COMPREHENSIONS):
        own, inner_bound = _comprehension_levels(node, bound, limit)
    children = (c for c in ast.iter_child_nodes(node) if not isinstance(c, SCOPE_NODES))
    return own + max((loop_depth(c, limit, inner_bound) for c in children), default=0)


def _iteration_key(iterable: ast.AST) -> str:
    """Normalise 'range(len(x))' to the dump of x so it matches iteration over x."""
    if (isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Name) and iterable.func.id == "range"
            and len(iterable.args) == 1 and isinstance(iterable.args[0], ast.Call)
            and isinstance(iterable.args[0].func, ast.Name) and iterable.args[0].func.id == "len"
            and len(iterable.args[0].args) == 1):
        return ast.dump(iterable.args[0].args[0])
    return ast.dump(iterable)


def _pairwise_same_collection(outer: ast.AST) -> bool:
    """True when a loop contains another loop over the same collection (all-pairs pattern)."""
    if isinstance(outer, (ast.For, ast.AsyncFor)):
        key, skip = _iteration_key(outer.iter), None
    elif isinstance(outer, _COMPREHENSIONS):
        key, skip = _iteration_key(outer.generators[0].iter), outer.generators[0]
    else:
        return False
    inner_iterables = []
    for node in own_nodes(outer):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            inner_iterables.append(node.iter)
        elif isinstance(node, ast.comprehension) and node is not skip:
            inner_iterables.append(node.iter)
    return any(_iteration_key(it) == key for it in inner_iterables)


def _is_list_expr(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.ListComp)):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "list"


def _is_str_expr(node: ast.AST) -> bool:
    if is_str_const(node) or isinstance(node, ast.JoinedStr):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"join", "strip", "lower", "upper", "format"}) or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str")


class _Visitor(ast.NodeVisitor):
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.depth = 0
        self.list_names: set[str] = set()
        self.str_names: set[str] = set()

    def _enter_loop(self, node: ast.AST) -> None:
        if self.depth == 0:
            self._report_nest(node)
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def _report_nest(self, node: ast.AST) -> None:
        config = self.ctx.config
        levels = loop_depth(node, config.small_iteration_limit)
        if levels >= max(2, config.min_loop_nest_depth):
            severity, confidence = _SEVERITY_BY_DEPTH.get(levels, ("high", "medium"))
            self.ctx.report("PERF101", node, f"{levels} seviyeli iç içe döngü",
                            severity=severity, confidence=confidence)
        elif levels >= 2 and _pairwise_same_collection(node):
            self.ctx.report("PERF101", node, "Aynı koleksiyonda tüm çiftler üzerinde döngü (karesel maliyet)",
                            severity="medium", confidence="medium")

    visit_For = visit_AsyncFor = visit_While = _enter_loop
    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _enter_loop

    def _visit_scope(self, node: ast.AST) -> None:
        saved, self.depth = self.depth, 0
        self.generic_visit(node)
        self.depth = saved

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _visit_scope

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                if _is_list_expr(node.value):
                    self.list_names.add(target.id)
                else:
                    self.list_names.discard(target.id)
                if _is_str_expr(node.value):
                    self.str_names.add(target.id)
                else:
                    self.str_names.discard(target.id)
        if self.depth:
            self._list_concat(node)
        self.generic_visit(node)

    def _list_concat(self, node: ast.Assign) -> None:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        name = node.targets[0].id
        value = node.value
        if not (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)):
            return
        left, right = value.left, value.right
        self_left = isinstance(left, ast.Name) and left.id == name and _is_list_expr(right)
        self_right = isinstance(right, ast.Name) and right.id == name and _is_list_expr(left)
        if self_left or self_right:
            self.ctx.report("PERF103", node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if self.depth and isinstance(func, ast.Attribute) and func.attr == "count" and node.args:
            receiver = func.value
            known_str = is_str_const(receiver) or isinstance(receiver, ast.JoinedStr) or (
                isinstance(receiver, ast.Name) and receiver.id in self.str_names)
            if not known_str:
                known_list = isinstance(receiver, ast.Name) and receiver.id in self.list_names
                severity, confidence = ("medium", "medium") if known_list else ("low", "low")
                self.ctx.report("PERF102", node, severity=severity, confidence=confidence)
        self.generic_visit(node)


def run(ctx: Context) -> None:
    """Run the performance heuristics."""
    _Visitor(ctx).visit(ctx.tree)
