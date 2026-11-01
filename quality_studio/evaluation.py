"""Measure rule quality against the hand-labeled ground truth."""
from __future__ import annotations

import math

from .config import Config
from .engine import analyze
from .ground_truth import CASES, Case


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion; None when total is 0."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 3)


def evaluate(cases: list[Case] | None = None, config: Config | None = None) -> dict:
    """Run the AST engine on every case and score predictions per rule and overall."""
    cases = CASES if cases is None else cases
    counts: dict[str, dict[str, int]] = {}
    mismatches = []
    for case in cases:
        predicted = {f.rule for f in analyze(case.code, config).findings}
        for rule in predicted | case.expected:
            bucket = counts.setdefault(rule, {"tp": 0, "fp": 0, "fn": 0})
            if rule in predicted and rule in case.expected:
                bucket["tp"] += 1
            elif rule in predicted:
                bucket["fp"] += 1
            else:
                bucket["fn"] += 1
        if predicted != case.expected:
            mismatches.append({
                "case": case.id,
                "missed": sorted(case.expected - predicted),
                "unexpected": sorted(predicted - case.expected),
                "note": case.note,
            })

    rules = {}
    for rule in sorted(counts):
        c = counts[rule]
        precision = _ratio(c["tp"], c["tp"] + c["fp"])
        recall = _ratio(c["tp"], c["tp"] + c["fn"])
        rules[rule] = {**c, "support": c["tp"] + c["fn"], "precision": precision, "recall": recall,
                       "f1": _f1(precision, recall)}

    tp = sum(c["tp"] for c in counts.values())
    fp = sum(c["fp"] for c in counts.values())
    fn = sum(c["fn"] for c in counts.values())
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {
        "cases": len(cases),
        "positive_labels": tp + fn,
        "rules": rules,
        "micro": {
            "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": _f1(precision, recall),
            "precision_ci95": wilson_interval(tp, tp + fp),
            "recall_ci95": wilson_interval(tp, tp + fn),
        },
        "mismatches": mismatches,
        "caveat": ("Etiketleri bu aracın yazarı hazırladı: küçük ve sentetik bir kümede regresyon ve akıl sağlığı "
                   "testidir, gerçek dünya kodundaki doğruluğun tahmini değildir."),
    }
