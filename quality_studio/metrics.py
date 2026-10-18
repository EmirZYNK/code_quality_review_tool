"""Single source of truth for complexity, size metrics and the quality score."""
from __future__ import annotations

import ast
from collections.abc import Iterable

from .models import NON_DEFECT_CATEGORIES, SEVERITY_WEIGHT, Finding
from .astutil import FUNCTION_NODES, own_nodes

LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)
MIN_SCORE_LINES = 30
SCORE_SCALE = 25.0


def decision_points(scope: ast.AST) -> int:
    """Count McCabe decision points inside one scope (nested scopes excluded)."""
    total = 0
    for node in own_nodes(scope):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
            total += 1
        elif isinstance(node, ast.comprehension):
            total += 1 + len(node.ifs)
        elif isinstance(node, ast.BoolOp):
            total += len(node.values) - 1
        elif isinstance(node, ast.match_case):
            total += 1
    return total


def function_complexity(func: ast.AST) -> int:
    """Cyclomatic complexity of one function (own scope only)."""
    return 1 + decision_points(func)


def module_complexity(tree: ast.Module) -> tuple[int, int]:
    """Return (total complexity, highest single-function complexity)."""
    module_level = 1 + decision_points(tree)
    per_function = [function_complexity(n) - 1 for n in ast.walk(tree) if isinstance(n, FUNCTION_NODES)]
    # Lambdas and class bodies are separate scopes: count their decision points too.
    extra = sum(decision_points(n) for n in ast.walk(tree) if isinstance(n, (ast.Lambda, ast.ClassDef)))
    total = module_level + sum(per_function) + extra
    highest = max((c + 1 for c in per_function), default=0)
    return total, highest


def structure_metrics(code: str, tree: ast.Module) -> dict:
    """Size and structure counts that do not depend on rule findings."""
    lines = code.splitlines()
    nodes = list(ast.walk(tree))
    total, highest = module_complexity(tree)
    return {
        "lines": len(lines),
        "nonblank_lines": sum(bool(line.strip()) for line in lines),
        "functions": sum(isinstance(n, FUNCTION_NODES) for n in nodes),
        "classes": sum(isinstance(n, ast.ClassDef) for n in nodes),
        "loops": sum(isinstance(n, LOOP_NODES) for n in nodes),
        "branches": sum(isinstance(n, (ast.If, ast.IfExp, ast.Match, ast.Try)) for n in nodes),
        "ast_nodes": len(nodes),
        "complexity_total": total,
        "complexity_max_function": highest,
    }


def weighted_penalty(findings: Iterable[Finding]) -> int:
    """Severity-weighted sum of defect findings (AI indicators excluded)."""
    return sum(
        SEVERITY_WEIGHT[f.severity] for f in findings if f.category not in NON_DEFECT_CATEGORIES
    )


def quality_score(findings: Iterable[Finding], nonblank_lines: int) -> int:
    """Heuristic 1-100 score from severity-weighted findings per 100 non-blank lines.

    score = 100 / (1 + density / 25), density = penalty * 100 / max(lines, 30).
    Strictly decreasing in the penalty (no saturation at 0) and size-aware: the same
    findings weigh less in a longer file. AI-pattern indicators are excluded.
    """
    density = weighted_penalty(findings) * 100 / max(nonblank_lines, MIN_SCORE_LINES)
    return max(1, round(100 / (1 + density / SCORE_SCALE)))


def finding_density(findings: Iterable[Finding], nonblank_lines: int, *, ai_patterns: bool = False) -> float:
    """Findings per 100 non-blank lines, for defects or (ai_patterns=True) AI indicators."""
    count = sum((f.category in NON_DEFECT_CATEGORIES) == ai_patterns for f in findings)
    return count * 100 / max(nonblank_lines, 1)
