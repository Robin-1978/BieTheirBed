"""Single authority for application runtime paths."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def default_runtime_root() -> Path:
    """Return the per-user application state directory."""
    configured = os.environ.get("KNOA_RUNTIME_ROOT") or os.environ.get(
        "KNOA_HOME"
    )
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".knoa"


def os_runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "knoa"
    return Path.home() / ".local" / "run" / "knoa"


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
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def mcp(self) -> Path:
        return self.root / "mcp"

    @property
    def mcp_secrets(self) -> Path:
        return self.secrets / "mcp"

    @property
    def service_env(self) -> Path:
        return self.config / "service.env"

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


def load_service_environment(root: str | Path | None = None) -> None:
    """Load private service variables without allowing shell evaluation."""

    path = RuntimePaths.from_root(root).service_env
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError("Knoa service environment must be a regular file")
    metadata = path.stat()
    if metadata.st_mode & 0o077:
        raise PermissionError("Knoa service environment must use mode 0600")
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"Invalid Knoa service environment entry at line {line_number}"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
            raise ValueError(
                f"Invalid Knoa service environment name at line {line_number}"
            )
        normalized = value.strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
            normalized = normalized[1:-1]
        os.environ.setdefault(name, normalized)
