"""Single authority for application runtime paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def os_runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "pc-assistant"
    return Path.home() / ".local" / "run" / "pc-assistant"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @classmethod
    def from_root(cls, root: str | Path = ".") -> "RuntimePaths":
        return cls(Path(root).expanduser().resolve())

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def attachments(self) -> Path:
        return self.root / "attachments"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def socket(self) -> Path:
        return os_runtime_dir() / "service.sock"

    @property
    def pid(self) -> Path:
        return os_runtime_dir() / "service.pid"

    def resolve(self, value: str | Path, *, default_parent: Path | None = None) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        if path.parent == Path(".") and default_parent is not None:
            return default_parent / path.name
        return self.root / path
