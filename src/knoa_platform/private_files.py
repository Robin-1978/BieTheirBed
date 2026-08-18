"""Cross-platform private file primitives for Hub and Node state."""

from __future__ import annotations

import os
import stat
from pathlib import Path


IS_WINDOWS = os.name == "nt"


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def prepare_private_directory(path: str | Path, *, label: str) -> Path:
    """Create a private state directory and validate POSIX ownership.

    Windows confidentiality is enforced by the NTFS ACL installed on the
    Knoa state root. Python's POSIX mode bits do not describe a Windows ACL,
    so runtime validation there is limited to file type and symlink checks.
    """

    resolved = _absolute(path)
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise RuntimeError(f"{label} must be a regular directory")
    if not IS_WINDOWS:
        owner = getattr(os, "geteuid")()
        if resolved.stat().st_uid != owner:
            raise RuntimeError(f"{label} has the wrong owner")
        resolved.chmod(0o700)
    return resolved


def restrict_private_file(path: str | Path) -> Path:
    resolved = Path(path)
    if not IS_WINDOWS:
        resolved.chmod(0o600)
    return resolved


def validate_private_file(
    path: str | Path,
    *,
    label: str,
    max_bytes: int | None = None,
) -> Path:
    resolved = _absolute(path)
    metadata = resolved.lstat()
    if resolved.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"{label} must be a regular non-symlink file")
    if not IS_WINDOWS:
        owner = getattr(os, "geteuid")()
        if metadata.st_uid != owner:
            raise RuntimeError(f"{label} has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RuntimeError(f"{label} must be owner-only")
    if max_bytes is not None and metadata.st_size > max_bytes:
        raise RuntimeError(f"{label} exceeds {max_bytes} bytes")
    return resolved


def prepare_private_file(path: str | Path, *, label: str) -> Path:
    resolved = _absolute(path)
    prepare_private_directory(resolved.parent, label=f"{label} directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags, 0o600)
    except FileExistsError:
        return validate_private_file(resolved, label=label)
    else:
        os.close(descriptor)
        restrict_private_file(resolved)
        return resolved


def fsync_directory(path: str | Path) -> None:
    """Persist a directory entry where the host platform supports it."""

    if IS_WINDOWS:
        return
    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_exclusive_file(temporary: Path, destination: Path) -> None:
    """Publish a fully flushed file without replacing an existing identity."""

    if IS_WINDOWS:
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)
    fsync_directory(destination.parent)


__all__ = [
    "IS_WINDOWS",
    "fsync_directory",
    "prepare_private_directory",
    "prepare_private_file",
    "publish_exclusive_file",
    "restrict_private_file",
    "validate_private_file",
]
