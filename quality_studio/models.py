"""Shared data structures and severity vocabulary."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_WEIGHT = {"info": 0, "low": 1, "medium": 2, "high": 5, "critical": 10}

CATEGORIES = (
    "Security",
    "Performance",
    "Code Smell",
    "Style",
    "Reliability",
    "Redundancy",
    "AI Pattern",
    "Syntax",
)

# Arayüzde ve raporlarda gösterilen Türkçe adlar (iç anahtarlar İngilizce kalır).
CATEGORY_TR = {
    "Security": "Güvenlik",
    "Performance": "Performans",
    "Code Smell": "Kod Kokusu",
    "Style": "Stil",
    "Reliability": "Güvenilirlik",
    "Redundancy": "Gereksiz Kod",
    "AI Pattern": "YZ Göstergesi",
    "Syntax": "Sözdizimi",
}
SEVERITY_TR = {"critical": "kritik", "high": "yüksek", "medium": "orta", "low": "düşük", "info": "bilgi"}
CONFIDENCE_TR = {"high": "yüksek", "medium": "orta", "low": "düşük"}

# Categories that describe probabilistic indicators rather than defects.
# They are reported but never lower the quality score.
NON_DEFECT_CATEGORIES = frozenset({"AI Pattern"})


@dataclass(frozen=True)
class Finding:
    """One diagnostic produced by a rule or an external tool."""

    rule: str
    category: str
    severity: str
    line: int
    end_line: int
    message: str
    why: str
    fix: str
    source: str = "AST"
    confidence: str = "high"
    cwe: str | None = None
    corroborated_by: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Plain-dict form for JSON export."""
        data = asdict(self)
        data["corroborated_by"] = list(self.corroborated_by)
        return data


@dataclass
class AnalysisResult:
    """Outcome of analysing one source text."""

    valid: bool
    findings: list[Finding] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    suppressed: int = 0
    external: dict = field(default_factory=dict)

    @property
    def issues(self) -> list[dict]:
        """Findings as plain dicts."""
        return [f.to_dict() for f in self.findings]

    def to_dict(self) -> dict:
        """Plain-dict form for JSON export."""
        return {
            "valid": self.valid,
            "findings": self.issues,
            "metrics": self.metrics,
            "suppressed": self.suppressed,
            "external": self.external,
        }
