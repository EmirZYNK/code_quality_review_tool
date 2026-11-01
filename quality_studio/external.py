"""Optional adapters that cross-validate AST findings with ruff and bandit.

Both tools only read the source as text. They are used when installed and silently
skipped otherwise; the AST engine never depends on them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from .models import Finding

RUFF_SELECT = "F,B,S,PERF,E7"

# external code -> our rule id, used to de-duplicate and to record corroboration.
RUFF_EQUIVALENT = {
    "S307": "SEC101", "S102": "SEC101", "S301": "SEC102", "S602": "SEC103", "S604": "SEC103",
    "S605": "SEC106", "S608": "SEC104", "S105": "SEC105", "S106": "SEC105", "S107": "SEC105",
    "S506": "SEC107", "S324": "SEC108", "S501": "SEC109", "S101": "SEC110", "S306": "SEC111",
    "S311": "SEC112", "S113": "SEC114",
    "F401": "RED001", "F841": "RED002", "E711": "CQ104", "E712": "CQ106", "B006": "CQ105",
    "E722": "CQ201", "S110": "CQ202",
}
BANDIT_EQUIVALENT = {
    "B307": "SEC101", "B102": "SEC101", "B301": "SEC102", "B602": "SEC103", "B604": "SEC103",
    "B605": "SEC106", "B608": "SEC104", "B105": "SEC105", "B106": "SEC105", "B107": "SEC105",
    "B506": "SEC107", "B303": "SEC108", "B324": "SEC108", "B501": "SEC109", "B101": "SEC110",
    "B306": "SEC111", "B311": "SEC112", "B113": "SEC114", "B110": "CQ202",
}
_BANDIT_SEVERITY = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}


def _ruff_category(code: str) -> tuple[str, str]:
    if code in {"F401", "F841"}:
        return "Redundancy", "low"
    if code.startswith("S"):
        return "Security", "medium"
    if code.startswith("PERF"):
        return "Performance", "low"
    if code.startswith("E"):
        return "Style", "low"
    return "Reliability", "medium"


def parse_ruff(payload: str) -> list[Finding]:
    """Convert ruff's JSON output into findings."""
    findings = []
    for item in json.loads(payload or "[]"):
        code = item.get("code") or "RUFF"
        category, severity = _ruff_category(code)
        line = item["location"]["row"]
        end = (item.get("end_location") or item["location"])["row"]
        findings.append(Finding(
            rule=f"RUFF:{code}", category=category, severity=severity, line=line, end_line=max(line, end),
            message=item.get("message", code), why="Reported by ruff.", fix="See the ruff rule documentation.",
            source="ruff", confidence="medium"))
    return findings


def parse_bandit(payload: str) -> list[Finding]:
    """Convert bandit's JSON output into findings."""
    findings = []
    for item in json.loads(payload or "{}").get("results", []):
        line = item.get("line_number", 1)
        cwe = (item.get("issue_cwe") or {}).get("id")
        findings.append(Finding(
            rule=f"BANDIT:{item.get('test_id', 'B000')}", category="Security",
            severity=_BANDIT_SEVERITY.get(str(item.get("issue_severity", "LOW")).upper(), "low"),
            line=line, end_line=line, message=item.get("issue_text", "Bandit finding"),
            why="Reported by bandit.", fix="See the bandit test documentation.", source="bandit",
            confidence=str(item.get("issue_confidence", "medium")).lower(),
            cwe=f"CWE-{cwe}" if cwe else None))
    return findings


def _run(command: list[str], *, stdin: str | None, timeout: int) -> subprocess.CompletedProcess:
    # List arguments, no shell: the analysed text is passed as data only.
    return subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)


def run_external(code: str, timeout: int = 20) -> tuple[list[Finding], dict]:
    """Run whichever of ruff/bandit is installed. Returns (findings, status per tool)."""
    findings: list[Finding] = []
    status: dict[str, str] = {}
    if shutil.which("ruff"):
        try:
            proc = _run(["ruff", "check", "--isolated", "--no-cache", "--output-format", "json",
                         "--select", RUFF_SELECT, "--stdin-filename", "snippet.py", "-"],
                        stdin=code, timeout=timeout)
            findings += parse_ruff(proc.stdout)
            status["ruff"] = "ok"
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
            status["ruff"] = f"failed: {type(exc).__name__}"
    else:
        status["ruff"] = "not installed"
    if shutil.which("bandit"):
        try:
            with tempfile.TemporaryDirectory(prefix="cqs_bandit_") as folder:
                target = Path(folder) / "snippet.py"
                target.write_text(code, encoding="utf-8")
                proc = _run(["bandit", "-q", "-f", "json", str(target)], stdin=None, timeout=timeout)
            findings += parse_bandit(proc.stdout)
            status["bandit"] = "ok"
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
            status["bandit"] = f"failed: {type(exc).__name__}"
    else:
        status["bandit"] = "not installed"
    return findings, status


def _equivalent_rule(finding: Finding) -> str | None:
    tool, _, code = finding.rule.partition(":")
    table = RUFF_EQUIVALENT if tool == "RUFF" else BANDIT_EQUIVALENT
    return table.get(code)


def merge(ast_findings: list[Finding], external: list[Finding]) -> list[Finding]:
    """Attach external confirmations to matching AST findings; keep unmatched external ones."""
    corroboration: dict[int, list[str]] = {}
    extras: list[Finding] = []
    for ext in external:
        target = _equivalent_rule(ext)
        matched = False
        if target:
            for index, own in enumerate(ast_findings):
                if own.rule == target and own.line <= ext.line <= own.end_line:
                    corroboration.setdefault(index, []).append(f"{ext.source}:{ext.rule.split(':')[1]}")
                    matched = True
        if not matched:
            extras.append(ext)  # not covered by an AST rule, or the AST engine missed it
    merged = []
    for index, own in enumerate(ast_findings):
        tools = corroboration.get(index)
        if tools:
            own = replace(own, corroborated_by=tuple(sorted(set(tools))))
        merged.append(own)
    return merged + extras
