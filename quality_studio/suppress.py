"""Comment extraction and '# noqa' style suppression."""
from __future__ import annotations

import contextlib
import io
import re
import tokenize
from dataclasses import dataclass

_NOQA = re.compile(r"#\s*noqa(?:\s*:\s*(?P<codes>[A-Za-z0-9_,\s]+))?", re.IGNORECASE)
_IGNORE = re.compile(r"#\s*cqs:\s*ignore(?:\[(?P<codes>[^\]]*)\])?", re.IGNORECASE)


@dataclass(frozen=True)
class Comment:
    line: int
    text: str
    standalone: bool


def extract_comments(source: str) -> list[Comment]:
    """Return all comments; tolerant of tokenizer errors (returns what was read)."""
    comments: list[Comment] = []
    with contextlib.suppress(tokenize.TokenError, IndentationError, SyntaxError):
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                standalone = not token.line[: token.start[1]].strip()
                comments.append(Comment(token.start[0], token.string, standalone))
    return comments


def suppression_map(comments: list[Comment]) -> dict[int, set[str] | None]:
    """Map line -> set of suppressed rule ids, or None meaning 'all rules'."""
    result: dict[int, set[str] | None] = {}
    for comment in comments:
        for pattern in (_NOQA, _IGNORE):
            match = pattern.search(comment.text)
            if not match:
                continue
            codes = match.group("codes")
            if not codes or not codes.strip():
                result[comment.line] = None
            else:
                parsed = {c.strip().upper() for c in re.split(r"[,\s]+", codes) if c.strip()}
                existing = result.get(comment.line, set())
                result[comment.line] = None if existing is None else existing | parsed
    return result


def is_suppressed(rule: str, line: int, table: dict[int, set[str] | None]) -> bool:
    """True when a noqa/ignore comment on the line covers the rule."""
    if line not in table:
        return False
    codes = table[line]
    return codes is None or rule.upper() in codes
