"""Private credential management for the loopback Core WebSocket service."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from pc_assistant.runtime import RuntimePaths


_MAX_TOKEN_BYTES = 4096


def resolve_local_service_token(paths: RuntimePaths) -> str:
    """Return the private, persistent credential for local Core clients."""
    return _load_or_create(paths.config / "service.token")


def _load_or_create(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.geteuid():
        raise RuntimeError(f"Service credential directory has the wrong owner: {path.parent}")
    path.parent.chmod(0o700)

    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_private_token(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)
    return token


def _read_private_token(path: Path) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Service credential must be a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise RuntimeError(f"Service credential has the wrong owner: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError(f"Service credential must be owner-only: {path}")
    if metadata.st_size > _MAX_TOKEN_BYTES:
        raise RuntimeError(f"Service credential exceeds {_MAX_TOKEN_BYTES} bytes: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"Service credential is empty: {path}")
    return token
