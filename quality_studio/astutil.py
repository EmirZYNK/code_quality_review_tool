"""AST helper functions shared by rules and metrics."""
from __future__ import annotations

import ast

SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def own_nodes(root: ast.AST):
    """Yield descendants of root without entering nested function/class/lambda scopes."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPE_NODES):
            stack.extend(ast.iter_child_nodes(node))


def statement_lists(tree: ast.AST):
    """Yield every list of statements (bodies, else/finally blocks, handlers' bodies)."""
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            block = getattr(node, attr, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                yield block


def is_str_const(node: ast.AST) -> bool:
    """True for a string literal node."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def is_constant_text(node: ast.AST) -> bool:
    """True for string literals and concatenations made only of string literals."""
    if is_str_const(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return is_constant_text(node.left) and is_constant_text(node.right)
    return False


def is_dynamic_string(node: ast.AST) -> bool:
    """True when a string is assembled from non-literal parts (f-string, +, %, .format)."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            if is_constant_text(node):
                return False
            return any(_stringish(side) for side in (node.left, node.right))
        if isinstance(node.op, ast.Mod):
            return is_str_const(node.left) and not isinstance(node.right, ast.Constant)
        return False
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format" and is_str_const(node.func.value)):
        return any(not isinstance(a, ast.Constant) for a in node.args) or bool(node.keywords)
    return False


def _stringish(node: ast.AST) -> bool:
    return is_str_const(node) or isinstance(node, ast.JoinedStr) or is_dynamic_string(node)
