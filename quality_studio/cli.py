"""Command-line interface: analyse files, directories or ZIP archives.

Exit codes: 0 = no findings at or above --fail-on, 1 = findings found, 2 = usage or input error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, Config
from .engine import analyze
from .models import SEVERITY_ORDER
from .project_scan import analyzed, scan_archive, scan_directory
from .report import to_json, to_sarif, to_text
from .rules import RULES

FORMATS = {"text": to_text, "json": to_json, "sarif": to_sarif}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality-studio", description="Python için statik kod kalitesi analizi")
    parser.add_argument("paths", type=Path, nargs="+", help="Python dosyaları, klasörler veya .zip arşivleri")
    parser.add_argument("--format", choices=sorted(FORMATS), default="text")
    parser.add_argument("--fail-on", choices=sorted(SEVERITY_ORDER, key=SEVERITY_ORDER.get), default="medium",
                        help="çıkış kodunu sıfırdan farklı yapan en düşük önem (varsayılan: medium)")
    parser.add_argument("--disable", default="", help="kapatılacak kural kimlikleri (virgülle ayrılmış)")
    parser.add_argument("--config", type=Path, help="eşik değerlerini değiştiren JSON dosyası")
    parser.add_argument("--external", action="store_true", help="kuruluysa ruff/bandit ile de çapraz doğrula")
    parser.add_argument("--output", type=Path, help="raporu ekrana yazmak yerine bu dosyaya yaz")
    return parser


def _load_config(args: argparse.Namespace) -> Config:
    config = DEFAULT_CONFIG
    if args.config:
        config = Config.from_mapping(json.loads(args.config.read_text(encoding="utf-8")))
    disabled = {r.strip().upper() for r in args.disable.split(",") if r.strip()}
    unknown = disabled - set(RULES)
    if unknown:
        raise ValueError(f"Bilinmeyen kural kimlikleri: {', '.join(sorted(unknown))}")
    return config.with_disabled(disabled)


def _collect(path: Path, config: Config, external: bool):
    if path.is_dir():
        return analyzed(scan_directory(path, config))
    if path.suffix.lower() == ".zip":
        return analyzed(scan_archive(path.read_bytes(), config))
    return [(path.name, analyze(path.read_text(encoding="utf-8-sig"), config, external=external))]


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = _parser().parse_args(argv)
    try:
        config = _load_config(args)
        entries = [item for path in args.paths for item in _collect(path, config, args.external)]
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"hata: {exc}", file=sys.stderr)
        return 2
    report = FORMATS[args.format](entries)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report)
    threshold = SEVERITY_ORDER[args.fail_on]
    failed = any(SEVERITY_ORDER[f.severity] >= threshold for _, result in entries for f in result.findings)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
