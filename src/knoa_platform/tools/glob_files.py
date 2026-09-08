from __future__ import annotations

from pathlib import Path
from typing import Any

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

_DEFAULT_IGNORED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".turbo",
    ".cursor",
})


class GlobFilesTool(ToolBase):
    name = "glob_files"
    description = (
        "Find files matching a glob pattern relative to a directory. "
        "Useful for exploring project structures and finding code files quickly."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.HOST_READ})
    risk = ToolRisk.LOW

    def __init__(self, working_directory: str = "") -> None:
        self._working_directory = working_directory

    def _resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute() and self._working_directory:
            p = Path(self._working_directory) / p
        return p.resolve()

    async def execute(self, **kwargs: Any) -> Any:
        pattern = str(kwargs.get("pattern", "")).strip()
        if not pattern:
            return {"error": "pattern is required (e.g. '**/*.py')"}

        raw_path = kwargs.get("path") or "."
        try:
            limit = int(kwargs.get("limit") or _DEFAULT_LIMIT)
        except (ValueError, TypeError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(limit, _MAX_LIMIT))

        try:
            root = self._resolve(raw_path)
            if not root.exists():
                return {"error": f"Path not found: {raw_path}"}
            if not root.is_dir():
                return {"error": f"Path is not a directory: {raw_path}"}
        except Exception as e:
            return {"error": str(e)}

        matched_files: list[str] = []
        truncated = False

        try:
            # If pattern does not start with ** or a slash, search recursively
            glob_pattern = pattern
            for p in root.glob(glob_pattern):
                # Filter out ignored directory components
                parts = p.parts
                if any(part in _DEFAULT_IGNORED_DIRS for part in parts):
                    continue
                if p.is_file():
                    try:
                        rel = str(p.relative_to(root))
                    except ValueError:
                        rel = str(p)
                    matched_files.append(rel)
                    if len(matched_files) >= limit:
                        truncated = True
                        break
        except Exception as e:
            return {"error": f"Invalid glob pattern or access error: {e}"}

        return {
            "pattern": pattern,
            "root": str(root),
            "matched_count": len(matched_files),
            "truncated": truncated,
            "files": sorted(matched_files),
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match files against (e.g. '**/*.py', 'src/**/*.ts')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search from (default: current directory)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of file paths to return (default: 100, max: 500)",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        }
