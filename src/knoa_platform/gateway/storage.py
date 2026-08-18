"""Private SQLite file preparation shared by Secure Gateway repositories."""
from __future__ import annotations

from pathlib import Path

from knoa_platform.private_files import prepare_private_file


def prepare_owner_only_database(path: str | Path, *, label: str) -> Path:
    return prepare_private_file(path, label=label)
