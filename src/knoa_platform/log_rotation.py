"""Bounded, compressed file logging shared by service entrypoints."""

from __future__ import annotations

import gzip
import logging
import os
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path

SERVICE_LOG_MAX_BYTES = 20 * 1024 * 1024
SERVICE_LOG_BACKUP_COUNT = 5
TRACE_LOG_MAX_BYTES = 20 * 1024 * 1024
TRACE_LOG_BACKUP_COUNT = 3


def _gzip_rotator(source: str, destination: str) -> None:
    with open(source, "rb") as source_stream:
        with gzip.open(destination, "wb", compresslevel=6) as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream)
    os.remove(source)


def compressed_rotating_file_handler(
    path: str | Path,
    *,
    max_bytes: int = SERVICE_LOG_MAX_BYTES,
    backup_count: int = SERVICE_LOG_BACKUP_COUNT,
    encoding: str | None = "utf-8",
) -> logging.Handler:
    """Create a size-bounded handler whose retained generations are gzip files."""
    if max_bytes <= 0 or backup_count <= 0:
        raise ValueError("Log rotation limits must be positive")
    resolved = Path(path)
    resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved.touch(exist_ok=True)
    resolved.chmod(0o600)
    handler = RotatingFileHandler(
        resolved,
        mode="a",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding=encoding,
    )
    handler.namer = lambda name: f"{name}.gz"
    handler.rotator = _gzip_rotator
    return handler


def rotate_compressed_file(
    path: str | Path,
    *,
    incoming_bytes: int = 0,
    max_bytes: int = TRACE_LOG_MAX_BYTES,
    backup_count: int = TRACE_LOG_BACKUP_COUNT,
) -> None:
    """Rotate an append-on-demand file before its next write."""
    if max_bytes <= 0 or backup_count <= 0:
        raise ValueError("Log rotation limits must be positive")
    resolved = Path(path)
    if not resolved.exists() or resolved.stat().st_size + incoming_bytes <= max_bytes:
        return
    oldest = resolved.with_name(f"{resolved.name}.{backup_count}.gz")
    oldest.unlink(missing_ok=True)
    for generation in range(backup_count - 1, 0, -1):
        source = resolved.with_name(f"{resolved.name}.{generation}.gz")
        if source.exists():
            source.replace(resolved.with_name(f"{resolved.name}.{generation + 1}.gz"))
    destination = resolved.with_name(f"{resolved.name}.1.gz")
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        _gzip_rotator(str(resolved), str(temporary))
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def compressed_generations(
    path: str | Path,
    *,
    backup_count: int = TRACE_LOG_BACKUP_COUNT,
) -> tuple[Path, ...]:
    """Return retained generations in chronological order, then the live file."""
    resolved = Path(path)
    archived = tuple(
        candidate
        for generation in range(backup_count, 0, -1)
        if (
            candidate := resolved.with_name(f"{resolved.name}.{generation}.gz")
        ).is_file()
    )
    return (*archived, resolved)


__all__ = [
    "SERVICE_LOG_BACKUP_COUNT",
    "SERVICE_LOG_MAX_BYTES",
    "TRACE_LOG_BACKUP_COUNT",
    "TRACE_LOG_MAX_BYTES",
    "compressed_generations",
    "compressed_rotating_file_handler",
    "rotate_compressed_file",
]
