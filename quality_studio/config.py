"""Analysis configuration: thresholds and rule switches."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Config:
    disabled: frozenset[str] = frozenset()
    max_source_bytes: int = 250_000
    max_function_lines: int = 50
    max_parameters: int = 6
    max_complexity: int = 15  # decision points incl. and/or, so stricter than plain McCabe
    min_loop_nest_depth: int = 3
    max_nesting: int = 4
    small_iteration_limit: int = 16
    duplicate_min_statements: int = 4
    duplicate_min_nodes: int = 25
    verbose_name_words: int = 5
    verbose_name_chars: int = 32
    restating_comment_min: int = 3

    def with_disabled(self, rules) -> "Config":
        """Return a copy with additional rule ids disabled."""
        return replace(self, disabled=self.disabled | frozenset(rules))

    @classmethod
    def from_mapping(cls, mapping: dict) -> "Config":
        """Build a Config from a dict; unknown keys raise ValueError."""
        known = {f.name for f in fields(cls)}
        unknown = set(mapping) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        values = dict(mapping)
        if "disabled" in values:
            values["disabled"] = frozenset(values["disabled"])
        return cls(**values)


DEFAULT_CONFIG = Config()
