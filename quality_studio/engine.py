"""Read-only AST analysis. Submitted code is parsed, never imported or executed."""
from __future__ import annotations

import ast

from .config import DEFAULT_CONFIG, Config
from .metrics import finding_density, quality_score, structure_metrics
from .models import SEVERITY_ORDER, AnalysisResult, Finding
from .rules import CHECKS, RULES
from .rules.context import Context
from .suppress import extract_comments, is_suppressed, suppression_map


def _syntax_result(error: Exception, config: Config) -> AnalysisResult:
    meta = RULES["SYN001"]
    line = getattr(error, "lineno", None) or 1
    message = getattr(error, "msg", None) or str(error)
    finding = Finding(
        rule=meta.id, category=meta.category, severity=meta.severity, line=line, end_line=line,
        message="Sözdizimi hatası", why=f"Python hatası: {message}", fix=meta.fix, confidence=meta.confidence,
    )
    return AnalysisResult(valid=False, findings=[finding], metrics={})


def _sort_key(finding: Finding):
    return (-SEVERITY_ORDER[finding.severity], finding.line, finding.rule)


def analyze(code: str, config: Config | None = None, *, external: bool = False) -> AnalysisResult:
    """Analyse Python source. Raises ValueError if the source exceeds the size limit.

    With external=True, ruff and bandit are also run when installed (they read the
    code as text; nothing is executed) and used to cross-validate AST findings.
    """
    config = config or DEFAULT_CONFIG
    if len(code.encode("utf-8")) > config.max_source_bytes:
        raise ValueError(f"Kaynak en fazla {config.max_source_bytes // 1000} KB olabilir")
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return _syntax_result(exc, config)

    comments = extract_comments(code)
    ctx = Context(tree, code, config, comments)
    try:
        for check in CHECKS:
            check(ctx)
        metrics = structure_metrics(code, tree)
    except RecursionError as exc:
        raise ValueError("Kaynak çözümlenemeyecek kadar derin iç içe") from exc

    findings = ctx.findings
    external_status: dict = {}
    if external:
        from .external import merge, run_external

        extra, external_status = run_external(code)
        findings = merge(findings, [f for f in extra if f.rule not in config.disabled])

    table = suppression_map(comments)
    kept = [f for f in findings if not is_suppressed(f.rule, f.line, table)]
    suppressed = len(findings) - len(kept)
    kept.sort(key=_sort_key)

    nonblank = metrics["nonblank_lines"]
    metrics.update(
        issue_count=len(kept),
        defect_count=sum(f.category != "AI Pattern" for f in kept),
        ai_indicator_count=sum(f.category == "AI Pattern" for f in kept),
        defects_per_100_lines=round(finding_density(kept, nonblank), 2),
        ai_indicators_per_100_lines=round(finding_density(kept, nonblank, ai_patterns=True), 2),
        score=quality_score(kept, nonblank),
    )
    return AnalysisResult(valid=True, findings=kept, metrics=metrics, suppressed=suppressed, external=external_status)
