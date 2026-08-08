"""Single authority for application runtime paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_runtime_root() -> Path:
    """Return the per-user application state directory."""
    configured = os.environ.get("PC_RUNTIME_ROOT") or os.environ.get(
        "PC_ASSISTANT_HOME"
    )
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".pc-assistant"


def os_runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "pc-assistant"
    return Path.home() / ".local" / "run" / "pc-assistant"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @classmethod
    def from_root(cls, root: str | Path | None = None) -> "RuntimePaths":
        selected = default_runtime_root() if root in (None, "") else Path(root)
        return cls(selected.expanduser().resolve())

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def attachments(self) -> Path:
        return self.root / "attachments"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def data(self) -> Path:
        return self.root / "data"

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
