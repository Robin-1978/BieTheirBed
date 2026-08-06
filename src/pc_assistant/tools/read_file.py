from __future__ import annotations
from pathlib import Path
from typing import Any
from pc_assistant.tools.base import ToolBase

_MAX_FILE_SIZE = 512_000  # 512KB

class ReadFileTool(ToolBase):
    name = "read_file"
    description = "Read a file's content."
    is_side_effecting = False

    def __init__(self, working_directory: str = "") -> None:
        self._working_directory = working_directory

    def _resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute() and self._working_directory:
            p = Path(self._working_directory) / p
        return p.resolve()

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
            if file_size > _MAX_FILE_SIZE and offset is None:
                # Read first chunk only
                text = p.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_SIZE]
                total_lines = text.count("\n") + 1
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

            if offset is not None or limit is not None:
                start = max(0, (offset or 1) - 1)  # convert 1-based to 0-based
                end = start + (limit or 100)
                selected = lines[start:end]
                content = "".join(selected)
                return {
                    "content": content,
                    "path": str(p),
                    "total_lines": total_lines,
                    "showing": f"lines {start+1}-{min(end, total_lines)}",
                }

            return {"content": text, "path": str(p), "total_lines": total_lines}

        except UnicodeDecodeError:
            return {"error": f"Cannot decode file as UTF-8: {path}"}
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
                    "offset": {"type": "integer", "description": "Start line (1-based)"},
                    "limit": {"type": "integer", "description": "Max lines to read"},
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
