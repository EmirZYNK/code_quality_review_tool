"""Rule modules. Each exposes run(ctx) and reports through ctx.report()."""
from . import ai_patterns, performance, redundancy, security, smell
from .catalog import AI_PATTERN_RULES, RULES, RuleMeta

CHECKS = (security.run, smell.run, performance.run, redundancy.run, ai_patterns.run)

__all__ = ["AI_PATTERN_RULES", "CHECKS", "RULES", "RuleMeta"]
