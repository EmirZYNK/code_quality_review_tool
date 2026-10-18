"""Shared test helpers."""
from quality_studio.engine import analyze


def rules(code, **kwargs):
    return {f.rule for f in analyze(code, **kwargs).findings}
