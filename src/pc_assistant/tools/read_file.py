from __future__ import annotations

from pathlib import Path
from typing import Any

from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk

_MAX_FILE_SIZE = 512_000  # 512KB
_MAX_LINE_COUNT = 1_000


class ReadFileTool(ToolBase):
    name = "read_file"
    description = "Read an existing local file, including files outside the current directory."
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

    @staticmethod
    def _read_line_window(path: Path, start_line: int, limit: int) -> dict[str, Any]:
        selected = bytearray()
        current_line = 0
        shown = 0
        truncated = False
        with path.open("rb") as stream:
            while current_line < start_line - 1:
                chunk = stream.readline(_MAX_FILE_SIZE + 1)
                if not chunk:
                    break
                while not chunk.endswith(b"\n") and len(chunk) > _MAX_FILE_SIZE:
                    chunk = stream.readline(_MAX_FILE_SIZE + 1)
                    if not chunk:
                        break
                current_line += 1

            while shown < limit:
                chunk = stream.readline(_MAX_FILE_SIZE + 1)
                if not chunk:
                    break
                current_line += 1
                shown += 1
                remaining = _MAX_FILE_SIZE - len(selected)
                if len(chunk) > remaining:
                    selected.extend(chunk[:remaining])
                    truncated = True
                    break
                selected.extend(chunk)
            if not truncated and shown == limit and stream.read(1):
                truncated = True

        showing = (
            f"lines {start_line}-{start_line + shown - 1}"
            if shown
            else "no lines"
        )
        return {
            "content": bytes(selected).decode("utf-8", errors="replace"),
            "path": str(path),
            "showing": showing,
            "truncated": truncated,
        }

    async def execute(self, **kwargs: Any) -> Any:
        path = kwargs.get("path", "")
        offset = kwargs.get("offset")  # 1-based line number
        limit = kwargs.get("limit")

        if not path:
            return {"error": "path is required"}

        try:
            p = self._resolve(path)
            if not p.exists():
                return {"error": f"File not found: {path}"}
            if p.is_dir():
                return {"error": f"Path is a directory: {path}"}

            file_size = p.stat().st_size
            if offset is not None or limit is not None:
                start_line = int(offset or 1)
                requested_lines = int(limit or 100)
                if start_line < 1 or start_line > 1_000_000:
                    return {"error": "offset must contain 1-1000000"}
                if requested_lines < 1 or requested_lines > _MAX_LINE_COUNT:
                    return {
                        "error": f"limit must contain 1-{_MAX_LINE_COUNT} lines"
                    }
                return self._read_line_window(p, start_line, requested_lines)

            if file_size > _MAX_FILE_SIZE:
                with p.open("rb") as stream:
                    raw = stream.read(_MAX_FILE_SIZE)
                text = raw.decode("utf-8", errors="replace")
                return {
                    "content": text,
                    "path": str(p),
                    "size": file_size,
                    "truncated": True,
                    "hint": "File too large. Use offset and limit to read specific sections.",
                }

            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            total_lines = len(lines)

            return {"content": text, "path": str(p), "total_lines": total_lines}

        except PermissionError:
            return {"error": f"Permission denied: {path}"}
        except Exception as e:
            return {"error": str(e)}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1_000_000,
                        "description": "Start line (1-based)",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_LINE_COUNT,
                        "description": "Max lines to read",
                    },
                },
                "required": ["path"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "start line (1-based)"},
                    "limit": {"type": "integer", "description": "max lines"},
                },
                "required": ["path"],
            },
        }
