"""Before/after impact from static analysis only. Never executes submitted code."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

from .config import Config
from .engine import analyze
from .models import Finding
from .refactor import apply

_STRUCTURE_KEYS = ("nonblank_lines", "functions", "branches", "loops", "ast_nodes",
                   "complexity_total", "complexity_max_function")


def _fingerprint(finding: Finding) -> tuple:
    return (finding.rule, finding.category, finding.severity, finding.message)


def _as_rows(counter: Counter) -> list[dict]:
    return [{"rule": k[0], "category": k[1], "severity": k[2], "message": k[3], "count": n}
            for k, n in counter.items()]


def impact(before: str, after: str, config: Config | None = None) -> dict:
    """Compare two versions. Returns {'valid': False, ...} unless both parse."""
    first, second = analyze(before, config), analyze(after, config)
    if not (first.valid and second.valid):
        return {"valid": False, "error": "Both versions must be syntactically valid Python."}
    b = Counter(map(_fingerprint, first.findings))
    a = Counter(map(_fingerprint, second.findings))
    resolved, introduced = b - a, a - b
    ops = SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False).get_opcodes()
    added = sum(j2 - j1 for tag, _i1, _i2, j1, j2 in ops if tag in ("insert", "replace"))
    removed = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in ops if tag in ("delete", "replace"))
    return {
        "valid": True,
        "before": {k: first.metrics[k] for k in _STRUCTURE_KEYS},
        "after": {k: second.metrics[k] for k in _STRUCTURE_KEYS},
        "score_before": first.metrics["score"],
        "score_after": second.metrics["score"],
        "score_delta": second.metrics["score"] - first.metrics["score"],
        "findings_before": len(first.findings),
        "findings_after": len(second.findings),
        "resolved": sum(resolved.values()),
        "introduced": sum(introduced.values()),
        "resolved_rules": _as_rows(resolved),
        "introduced_rules": _as_rows(introduced),
        "added_lines": added,
        "removed_lines": removed,
        "runtime": {
            "measured": False,
            "reason": "Submitted code is not executed. Static metrics cannot establish speed or memory use.",
        },
        "limitations": "The rule-based score and AST counts are proxies, not proof of correctness, "
                       "maintainability, security or performance.",
    }


def patch_effects(source: str, patches: list[dict], config: Config | None = None) -> list[dict | None]:
    """Static effect of applying each patch on its own (None if it cannot be applied cleanly)."""
    effects: list[dict | None] = []
    for index in range(len(patches)):
        try:
            patched = apply(source, patches, [index])
        except (ValueError, SyntaxError):
            effects.append(None)
            continue
        effect = impact(source, patched, config)
        if not effect["valid"]:
            effects.append(None)
            continue
        effects.append({key: effect[key] for key in
                        ("score_before", "score_after", "score_delta", "resolved", "introduced")})
    return effects
