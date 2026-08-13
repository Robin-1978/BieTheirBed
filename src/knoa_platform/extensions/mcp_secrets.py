"""Server-scoped private environment for locally supervised MCP processes."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re


def load_mcp_private_environment(path: str | Path) -> dict[str, str]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return {}
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError("MCP private environment must be a regular file")
    if resolved.stat().st_mode & 0o077:
        raise PermissionError("MCP private environment must use mode 0600")
    environment: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        resolved.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"Invalid MCP private environment entry at line {line_number}"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
            raise ValueError(
                f"Invalid MCP private environment name at line {line_number}"
            )
        normalized = value.strip()
        if (
            len(normalized) >= 2
            and normalized[0] == normalized[-1]
            and normalized[0] in {"'", '"'}
        ):
            normalized = normalized[1:-1]
        environment[name] = normalized
    return environment


def mcp_private_environment_loader(
    secret_root: str | Path | None,
    server_id: str,
) -> Callable[[], dict[str, str]] | None:
    if secret_root is None:
        return None
    path = Path(secret_root).expanduser().resolve() / f"{server_id}.env"
    return lambda: load_mcp_private_environment(path)
