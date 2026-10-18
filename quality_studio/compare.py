"""Labeled dataset comparison with per-sample statistics (not just pooled counts).

Each sample yields a defect density (findings per 100 non-blank lines). Groups are
compared with a Mann-Whitney U test, Cliff's delta as effect size and a bootstrap
confidence interval for the difference of means. Samples that fail to parse are counted
and reported per group, never dropped silently, because dropping them can bias results.
"""
from __future__ import annotations

import csv
import io
import itertools
import statistics

import numpy as np
from scipy import stats

from .config import Config
from .engine import analyze
from .metrics import finding_density

MAX_ROWS = 500
MAX_SAMPLE_CHARS = 250_000
MIN_GROUP_FOR_INFERENCE = 10


def load_dataset(raw: bytes | str, max_rows: int = MAX_ROWS, max_chars: int = MAX_SAMPLE_CHARS):
    """Parse a 'label,code' CSV into (label, code) pairs, with clear errors."""
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    previous = csv.field_size_limit(max_chars * 2)
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or not {"label", "code"} <= set(reader.fieldnames):
            found = ", ".join(reader.fieldnames or []) or "none"
            raise ValueError(f"CSV must have 'label' and 'code' columns (found: {found})")
        samples = []
        for number, row in enumerate(reader, start=2):
            if len(samples) >= max_rows:
                raise ValueError(f"Maximum {max_rows} samples")
            label, code = (row["label"] or "").strip(), row["code"] or ""
            if not label:
                raise ValueError(f"Row {number}: empty label")
            if len(code) > max_chars:
                raise ValueError(f"Row {number}: sample exceeds {max_chars // 1000} KB")
            samples.append((label, code))
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV: {exc}") from exc
    finally:
        csv.field_size_limit(previous)
    if not samples:
        raise ValueError("CSV contains no samples")
    return samples


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """P(a > b) - P(a < b); ranges from -1 to 1."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    greater = (x[:, None] > y[None, :]).sum()
    less = (x[:, None] < y[None, :]).sum()
    return float((greater - less) / (len(x) * len(y)))


def delta_magnitude(delta: float) -> str:
    """Label |Cliff's delta| using the Romano et al. thresholds."""
    size = abs(delta)
    if size < 0.147:
        return "negligible"
    if size < 0.33:
        return "small"
    if size < 0.474:
        return "medium"
    return "large"


def bootstrap_mean_difference(a: list[float], b: list[float], *, seed: int = 0,
                              resamples: int = 2000, level: float = 0.95) -> tuple[float, float]:
    """Percentile bootstrap interval for mean(a) - mean(b)."""
    rng = np.random.default_rng(seed)
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    diffs = np.empty(resamples)
    for i in range(resamples):
        diffs[i] = rng.choice(x, len(x)).mean() - rng.choice(y, len(y)).mean()
    tail = (1 - level) / 2 * 100
    return float(np.percentile(diffs, tail)), float(np.percentile(diffs, 100 - tail))


def compare_metric(a: list[float], b: list[float], *, seed: int = 0) -> dict:
    """Two-sample comparison of one per-sample metric (a minus b)."""
    if len(a) < 2 or len(b) < 2:
        return {"computable": False, "reason": "Each group needs at least 2 valid samples."}
    if len(set(a) | set(b)) == 1:
        u, p = len(a) * len(b) / 2, 1.0
    else:
        result = stats.mannwhitneyu(a, b, alternative="two-sided")
        u, p = float(result.statistic), float(result.pvalue)
    delta = cliffs_delta(a, b)
    low, high = bootstrap_mean_difference(a, b, seed=seed)
    return {
        "computable": True,
        "mean_a": round(statistics.fmean(a), 3), "mean_b": round(statistics.fmean(b), 3),
        "median_a": round(statistics.median(a), 3), "median_b": round(statistics.median(b), 3),
        "mean_difference": round(statistics.fmean(a) - statistics.fmean(b), 3),
        "ci95": [round(low, 3), round(high, 3)],
        "u_statistic": round(u, 3), "p_value": round(p, 6),
        "cliffs_delta": round(delta, 3), "effect_size": delta_magnitude(delta),
    }


def _collect_groups(samples, config: Config | None) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for label, code in samples:
        group = groups.setdefault(label, {"total": 0, "bad": 0, "defect": [], "ai": [], "lines": 0,
                                          "findings": 0, "categories": {}})
        group["total"] += 1
        result = analyze(code, config)
        if not result.valid:
            group["bad"] += 1
            continue
        nonblank = result.metrics["nonblank_lines"]
        group["defect"].append(finding_density(result.findings, nonblank))
        group["ai"].append(finding_density(result.findings, nonblank, ai_patterns=True))
        group["lines"] += nonblank
        group["findings"] += len(result.findings)
        for finding in result.findings:
            group["categories"][finding.category] = group["categories"].get(finding.category, 0) + 1
    return groups


def _summarize(groups: dict[str, dict]) -> dict[str, dict]:
    summary = {}
    for label, g in groups.items():
        valid = len(g["defect"])
        summary[label] = {
            "samples": g["total"],
            "valid_samples": valid,
            "syntax_errors": g["bad"],
            "syntax_error_rate": round(g["bad"] / g["total"], 3),
            "nonblank_lines": g["lines"],
            "findings": g["findings"],
            "pooled_findings_per_100_lines": round(g["findings"] * 100 / max(1, g["lines"]), 2),
            "mean_defect_density": round(statistics.fmean(g["defect"]), 3) if valid else None,
            "mean_ai_indicator_density": round(statistics.fmean(g["ai"]), 3) if valid else None,
            "category_counts": dict(g["categories"]),
        }
    return summary


def _compare_pair(a: str, b: str, groups: dict[str, dict], pair_count: int, seed: int) -> dict:
    ga, gb = groups[a], groups[b]
    entry = {"a": a, "b": b, "n_a": len(ga["defect"]), "n_b": len(gb["defect"]),
             "defect_density": compare_metric(ga["defect"], gb["defect"], seed=seed),
             "ai_indicator_density": compare_metric(ga["ai"], gb["ai"], seed=seed)}
    table = [[ga["bad"], ga["total"] - ga["bad"]], [gb["bad"], gb["total"] - gb["bad"]]]
    entry["syntax_error_fisher_p"] = round(float(stats.fisher_exact(table)[1]), 6)
    for key in ("defect_density", "ai_indicator_density"):
        if entry[key]["computable"]:
            entry[key]["p_adjusted_bonferroni"] = round(min(1.0, entry[key]["p_value"] * pair_count), 6)
    return entry


def _warnings(summary: dict[str, dict]) -> list[str]:
    warnings = []
    for label, s in summary.items():
        if s["syntax_errors"]:
            warnings.append(f"'{label}': {s['syntax_errors']} of {s['samples']} samples had syntax errors "
                            "and are excluded from density statistics (reported separately).")
        if s["valid_samples"] < MIN_GROUP_FOR_INFERENCE:
            warnings.append(f"'{label}': only {s['valid_samples']} valid samples; "
                            f"p-values and intervals are unreliable below {MIN_GROUP_FOR_INFERENCE}.")
    if len(summary) < 2:
        warnings.append("At least two labels are needed for a comparison.")
    return warnings


def evaluate_dataset(samples, config: Config | None = None, *, seed: int = 0) -> dict:
    """Analyse labeled samples and compare every pair of labels."""
    groups = _collect_groups(samples, config)
    summary = _summarize(groups)
    pairs = list(itertools.combinations(sorted(groups), 2))
    comparisons = [_compare_pair(a, b, groups, len(pairs), seed) for a, b in pairs]
    return {"labels": summary, "comparisons": comparisons, "warnings": _warnings(summary), "seed": seed}
