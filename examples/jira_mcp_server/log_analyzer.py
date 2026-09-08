"""Local diagnostic tool for unpacking and analyzing robot logs in evidence directories."""

from __future__ import annotations

import os
import re
import tarfile
import zipfile
from pathlib import Path
from typing import Any

_CRASH_PATTERNS = [
    re.compile(r"Segmentation fault", re.IGNORECASE),
    re.compile(r"\bSIGSEGV\b"),
    re.compile(r"\*\*\* Aborted at \d+"),
    re.compile(r"backtrace:", re.IGNORECASE),
    re.compile(r"Check failed:", re.IGNORECASE),
    re.compile(r"Assertion `.+` failed"),
    re.compile(r"Traceback \(most recent call last\):"),
]

_FATAL_PATTERNS = [
    re.compile(r"\bFATAL\b"),
    re.compile(r"\bCRITICAL\b"),
]

_ERROR_PATTERNS = [
    re.compile(r"\bERROR\b"),
]

_SOURCE_LINE_RE = re.compile(
    r"(?:\[|\b)([A-Za-z0-9_/-]+\.(?:cc|cpp|c|h|hpp|py)):([1-9][0-9]*)"
)

_ARCHIVE_EXTENSIONS = (".tar.gz", ".tgz", ".tar.bz2", ".tar", ".zip")
_MAX_UNPACK_BYTES = 500 * 1024 * 1024  # 500MB safety ceiling


def _is_safe_extraction(dest: Path, target: Path) -> bool:
    try:
        dest_res = dest.resolve()
        target_res = target.resolve()
        return target_res.is_relative_to(dest_res)
    except Exception:
        return False


def unpack_archive(archive_path: Path, extract_dir: Path) -> list[str]:
    """Safely unpack .tar.gz, .tgz, .zip archives with path traversal protection."""
    extract_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    unpacked_files: list[str] = []
    total_bytes = 0

    name_lower = archive_path.name.lower()
    if name_lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                total_bytes += member.file_size
                if total_bytes > _MAX_UNPACK_BYTES:
                    break
                target = extract_dir / member.filename
                if not _is_safe_extraction(extract_dir, target):
                    continue
                zf.extract(member, extract_dir)
                if not member.is_dir():
                    unpacked_files.append(str(target))
    elif any(name_lower.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar.bz2", ".tar")):
        mode = "r:*"
        with tarfile.open(archive_path, mode) as tf:
            for member in tf.getmembers():
                total_bytes += member.size
                if total_bytes > _MAX_UNPACK_BYTES:
                    break
                target = extract_dir / member.name
                if not _is_safe_extraction(extract_dir, target):
                    continue
                tf.extract(member, extract_dir)
                if member.isfile():
                    unpacked_files.append(str(target))

    return unpacked_files


def scan_file_for_errors(file_path: Path, max_matches: int = 20) -> dict[str, Any]:
    """Scan a single log file for crashes, fatal errors, and error samples."""
    crashes: list[dict[str, Any]] = []
    fatals: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return {"crashes": crashes, "fatals": fatals, "errors": errors}

    total_lines = len(lines)
    for idx, line in enumerate(lines):
        line_num = idx + 1
        line_stripped = line.strip()

        # Check for crash patterns
        for pat in _CRASH_PATTERNS:
            if pat.search(line_stripped):
                snippet = lines[max(0, idx - 3) : min(total_lines, idx + 8)]
                source_match = _SOURCE_LINE_RE.search(line_stripped)
                crashes.append(
                    {
                        "line": line_num,
                        "text": line_stripped[:500],
                        "source_file": source_match.group(1) if source_match else None,
                        "source_line": int(source_match.group(2)) if source_match else None,
                        "snippet": "".join(snippet)[:2000],
                    }
                )
                break

        # Check for FATAL
        for pat in _FATAL_PATTERNS:
            if pat.search(line_stripped):
                source_match = _SOURCE_LINE_RE.search(line_stripped)
                fatals.append(
                    {
                        "line": line_num,
                        "text": line_stripped[:500],
                        "source_file": source_match.group(1) if source_match else None,
                        "source_line": int(source_match.group(2)) if source_match else None,
                    }
                )
                break

        # Check for general ERROR (sampled)
        if len(errors) < max_matches:
            for pat in _ERROR_PATTERNS:
                if pat.search(line_stripped):
                    source_match = _SOURCE_LINE_RE.search(line_stripped)
                    errors.append(
                        {
                            "line": line_num,
                            "text": line_stripped[:300],
                            "source_file": source_match.group(1) if source_match else None,
                            "source_line": int(source_match.group(2)) if source_match else None,
                        }
                    )
                    break

    return {
        "file": str(file_path),
        "filename": file_path.name,
        "line_count": total_lines,
        "crashes": crashes[:max_matches],
        "fatals": fatals[:max_matches],
        "errors": errors[:max_matches],
    }


def analyze_directory_logs(evidence_dir: Path, auto_unpack: bool = True) -> dict[str, Any]:
    """Inspect evidence directory, unpack archives, and compile structured error analysis."""
    if not evidence_dir.is_dir():
        return {
            "status": "error",
            "message": f"Directory not found: {evidence_dir}",
            "crashes": [],
            "fatals": [],
            "errors": [],
            "analyzed_files": [],
        }

    unpacked_records: list[str] = []
    if auto_unpack:
        archives: list[Path] = []
        for root, _, files in os.walk(evidence_dir):
            # Avoid re-unpacking already unpacked subdirs
            if "unpacked" in Path(root).parts:
                continue
            for f in files:
                p = Path(root) / f
                if any(p.name.lower().endswith(ext) for ext in _ARCHIVE_EXTENSIONS):
                    archives.append(p)

        for arch in archives:
            extract_target = evidence_dir / "unpacked" / arch.stem
            unpacked = unpack_archive(arch, extract_target)
            unpacked_records.extend(unpacked)

    # Find all text/log candidates
    candidates: list[Path] = []
    for root, _, files in os.walk(evidence_dir):
        for f in files:
            p = Path(root) / f
            lower = p.name.lower()
            if any(lower.endswith(ext) for ext in (".log", ".txt", ".json", ".info", ".err")) or "." not in lower:
                candidates.append(p)

    all_crashes: list[dict[str, Any]] = []
    all_fatals: list[dict[str, Any]] = []
    all_errors: list[dict[str, Any]] = []
    analyzed_files: list[str] = []

    for file_path in candidates[:50]:  # Cap files to analyze
        if not file_path.is_file():
            continue
        analyzed_files.append(str(file_path))
        findings = scan_file_for_errors(file_path)
        for c in findings.get("crashes", []):
            all_crashes.append({"file": file_path.name, **c})
        for f in findings.get("fatals", []):
            all_fatals.append({"file": file_path.name, **f})
        for e in findings.get("errors", []):
            all_errors.append({"file": file_path.name, **e})

    return {
        "status": "success",
        "evidence_directory": str(evidence_dir),
        "unpacked_archives_count": len(unpacked_records),
        "analyzed_files_count": len(analyzed_files),
        "analyzed_files": [Path(p).name for p in analyzed_files],
        "crashes_count": len(all_crashes),
        "fatals_count": len(all_fatals),
        "errors_count": len(all_errors),
        "crashes": all_crashes[:30],
        "fatals": all_fatals[:30],
        "error_samples": all_errors[:20],
    }
