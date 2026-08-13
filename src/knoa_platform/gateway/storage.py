"""Private SQLite file preparation shared by Secure Gateway repositories."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def prepare_owner_only_database(path: str | Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.parent.stat().st_uid != os.geteuid():
        raise RuntimeError(f"{label} directory has the wrong owner")
    resolved.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags, 0o600)
    except FileExistsError:
        metadata = resolved.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"{label} must be a regular file")
        if metadata.st_uid != os.geteuid():
            raise RuntimeError(f"{label} has the wrong owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RuntimeError(f"{label} must be owner-only")
    else:
        os.close(descriptor)
    resolved.chmod(0o600)
    return resolved
