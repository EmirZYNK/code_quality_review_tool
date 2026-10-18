"""Shared analysis context handed to every rule module."""
from __future__ import annotations

import ast

from ..config import Config
from ..models import Finding
from ..suppress import Comment
from .catalog import RULES


def _build_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


class Context:
    """Holds the parsed tree, configuration and the findings collected so far."""

    def __init__(self, tree: ast.Module, source: str, config: Config, comments: list[Comment]):
        self.tree = tree
        self.source = source
        self.config = config
        self.comments = comments
        self.aliases = _build_aliases(tree)
        self.findings: list[Finding] = []

    def dotted(self, node: ast.AST) -> str:
        """Return the import-resolved dotted name of a Name/Attribute chain, else ''."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return ""
        parts.append(self.aliases.get(node.id, node.id))
        return ".".join(reversed(parts))

    def report(self, rule: str, node: ast.AST, message: str | None = None, *,
               severity: str | None = None, confidence: str | None = None, line: int | None = None) -> None:
        """Record a finding using the rule catalog defaults unless overridden."""
        if rule in self.config.disabled:
            return
        meta = RULES[rule]
        start = line if line is not None else getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", None) or start
        self.findings.append(Finding(
            rule=rule,
            category=meta.category,
            severity=severity or meta.severity,
            line=start,
            end_line=max(start, end),
            message=message or meta.name,
            why=meta.why,
            fix=meta.fix,
            confidence=confidence or meta.confidence,
            cwe=meta.cwe,
        ))
