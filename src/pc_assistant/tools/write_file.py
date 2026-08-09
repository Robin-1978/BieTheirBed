from __future__ import annotations
from pathlib import Path
from typing import Any
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class WriteFileTool(ToolBase):
    name = "write_file"
    description = "Create or overwrite a local file anywhere on the host."
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.HOST_WRITE})
    risk = ToolRisk.MEDIUM

    def __init__(self, working_directory: str = "") -> None:
        self._working_directory = working_directory

    def _resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute() and self._working_directory:
            p = Path(self._working_directory) / p
        return p.resolve()

    async def execute(self, **kwargs: Any) -> Any:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")

        if not path:
            return {"error": "path is required"}
        if content is None:
            content = ""

        try:
            p = self._resolve(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {
                "success": True,
                "path": str(p),
                "bytes_written": len(content.encode("utf-8")),
            }
        except PermissionError:
            return {"error": f"Permission denied: {path}"}
        except Exception as e:
            return {"error": str(e)}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "text to write; empty is allowed"},
                },
                "required": ["path", "content"],
            },
        }
