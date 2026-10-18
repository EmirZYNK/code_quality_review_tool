"""Static inspection of Python files inside a ZIP archive or a directory."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path, PurePosixPath

from .config import Config
from .engine import analyze
from .models import SEVERITY_ORDER, AnalysisResult

MAX_ARCHIVE_BYTES = 5_000_000
MAX_FILES = 200
MAX_TOTAL_UNCOMPRESSED = 5_000_000
MAX_FILE_BYTES = 250_000
IGNORED_DIRS = {"__pycache__", ".git", ".venv", "venv", "env", "node_modules", "site-packages",
                ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"}


def _ignored(path: PurePosixPath) -> bool:
    return any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in path.parts[:-1])


def _analyze_source(path: str, raw: bytes, config: Config | None) -> dict:
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"path": path, "source": "", "result": None, "error": "Not valid UTF-8; file skipped"}
    try:
        result = analyze(source, config)
    except ValueError as exc:
        return {"path": path, "source": source, "result": None, "error": str(exc)}
    return {"path": path, "source": source, "result": result, "error": None}


def _archive_candidates(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    candidates = []
    for info in archive.infolist():
        if info.is_dir() or not info.filename.endswith(".py"):
            continue
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts or info.flag_bits & 1:
            raise ValueError("Unsafe ZIP member path or encrypted file")
        if not _ignored(path):
            candidates.append((info, path))
    if len(candidates) > MAX_FILES:
        raise ValueError(f"Too many Python files (maximum {MAX_FILES})")
    return candidates


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_FILE_BYTES:
        raise ValueError(f"Python file too large: {info.filename}")
    try:
        with archive.open(info) as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Cannot read {info.filename}") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"Python file too large: {info.filename}")
    return raw


def scan_archive(payload: bytes, config: Config | None = None) -> list[dict]:
    """Analyse every .py file in a ZIP. Raises ValueError for unsafe or oversized archives."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP exceeds 5 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ValueError("Invalid ZIP archive") from exc
    files: list[dict] = []
    total = 0
    with archive:
        for info, path in _archive_candidates(archive):
            total += info.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("Uncompressed Python files exceed 5 MB")
            files.append(_analyze_source(str(path), _read_member(archive, info), config))
    if not files:
        raise ValueError("No .py files found in ZIP")
    return files


def scan_directory(root: Path, config: Config | None = None) -> list[dict]:
    """Analyse every .py file below root (symlinks are not followed)."""
    root = Path(root)
    files: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if path.is_symlink() or _ignored(relative):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            files.append({"path": str(relative), "source": "", "result": None, "error": "File too large; skipped"})
            continue
        files.append(_analyze_source(str(relative), path.read_bytes(), config))
        if len(files) > MAX_FILES:
            raise ValueError(f"Too many Python files (maximum {MAX_FILES})")
    if not files:
        raise ValueError("No .py files found")
    return files


def analyzed(files: list[dict]) -> list[tuple[str, AnalysisResult]]:
    """Keep only files that were successfully analysed, as (path, result)."""
    return [(f["path"], f["result"]) for f in files if f["result"] is not None]


def project_summary(files: list[dict]) -> dict:
    """Aggregate counts across a scan."""
    entries = analyzed(files)
    findings = [dict(f.to_dict(), file=path) for path, result in entries for f in result.findings]
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f["severity"]], f["file"], f["line"]))
    return {
        "files": len(files),
        "analyzed_files": len(entries),
        "skipped_files": sum(f["error"] is not None for f in files),
        "syntax_error_files": sum(not r.valid for _, r in entries),
        "issues": len(findings),
        "high_priority": sum(f["severity"] in ("high", "critical") for f in findings),
        "findings": findings,
    }
