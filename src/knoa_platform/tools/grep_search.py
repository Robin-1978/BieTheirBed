from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import re
from typing import Any

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk

_DEFAULT_MAX_RESULTS = 50
_UPPER_MAX_RESULTS = 200
_MAX_FILE_BYTES_TO_SCAN = 2 * 1024 * 1024  # 2MB

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


class GrepSearchTool(ToolBase):
    name = "grep_search"
    description = (
        "Search for a regex or text pattern across files in a directory. "
        "Returns matching relative paths, line numbers, and matching text snippets."
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
        pattern = kwargs.get("pattern")
        if not pattern:
            return {"error": "pattern is required"}

        raw_path = kwargs.get("path") or "."
        file_glob = str(kwargs.get("glob") or "*").strip()
        case_sensitive = bool(kwargs.get("case_sensitive", True))

        try:
            max_results = int(kwargs.get("max_results") or _DEFAULT_MAX_RESULTS)
        except (ValueError, TypeError):
            max_results = _DEFAULT_MAX_RESULTS
        max_results = max(1, min(max_results, _UPPER_MAX_RESULTS))

        try:
            root = self._resolve(raw_path)
            if not root.exists():
                return {"error": f"Path not found: {raw_path}"}
        except Exception as e:
            return {"error": str(e)}

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"error": f"Invalid regex pattern '{pattern}': {e}"}

        matches: list[dict[str, Any]] = []
        files_scanned = 0
        truncated = False

        if root.is_file():
            self._search_file(root, root.parent, regex, matches, max_results)
            files_scanned = 1
        else:
            for current_dir_str, dirs, files in os.walk(root):
                # Prune ignored directory trees in place
                dirs[:] = [d for d in dirs if d not in _DEFAULT_IGNORED_DIRS and not d.startswith(".")]
                current_dir = Path(current_dir_str)
                for fname in sorted(files):
                    if not fnmatch.fnmatch(fname, file_glob):
                        continue
                    file_path = current_dir / fname
                    files_scanned += 1
                    reached_limit = self._search_file(file_path, root, regex, matches, max_results)
                    if reached_limit:
                        truncated = True
                        break
                if truncated:
                    break

        return {
            "query": pattern,
            "root": str(root),
            "files_scanned": files_scanned,
            "matches_count": len(matches),
            "truncated": truncated,
            "matches": matches,
        }

    def _search_file(
        self,
        file_path: Path,
        root: Path,
        regex: re.Pattern,
        matches: list[dict[str, Any]],
        max_results: int,
    ) -> bool:
        try:
            stat = file_path.stat()
            if stat.st_size > _MAX_FILE_BYTES_TO_SCAN:
                return False
            with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                for line_idx, line in enumerate(f, start=1):
                    if regex.search(line):
                        try:
                            rel_path = str(file_path.relative_to(root))
                        except ValueError:
                            rel_path = str(file_path)
                        matches.append({
                            "file": rel_path,
                            "line": line_idx,
                            "content": line.rstrip("\r\n")[:300],
                        })
                        if len(matches) >= max_results:
                            return True
        except (OSError, PermissionError):
            pass
        return False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or literal pattern to search for in files",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: current directory)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Wildcard pattern to filter filenames (e.g. '*.py', '*.ts', default: '*')",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Case sensitivity (default: true)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (default: 50, max: 200)",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        }
