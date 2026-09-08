from __future__ import annotations

from pathlib import Path
from typing import Any

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class EditFileTool(ToolBase):
    name = "edit_file"
    description = (
        "Perform exact string replacement in a local text file. "
        "Preserves indentation and surrounding code. Fails safely if old_string is not found or ambiguous."
    )
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.HOST_WRITE, ToolCapability.HOST_READ})
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
        old_string = kwargs.get("old_string")
        new_string = kwargs.get("new_string")
        replace_all = bool(kwargs.get("replace_all", False))

        if not path:
            return {"error": "path is required"}
        if old_string is None:
            return {"error": "old_string is required"}
        if new_string is None:
            return {"error": "new_string is required"}
        if not old_string:
            return {"error": "old_string cannot be empty"}
        if old_string == new_string:
            return {"error": "new_string must be different from old_string"}

        try:
            p = self._resolve(path)
            if not p.exists():
                return {"error": f"File not found: {path}"}
            if not p.is_file():
                return {"error": f"Path is not a file: {path}"}

            content = p.read_text(encoding="utf-8")
            count = content.count(old_string)

            if count == 0:
                return {
                    "error": (
                        "old_string not found in file. Ensure exact indentation, "
                        "line breaks, and surrounding context match the source."
                    )
                }

            if count > 1 and not replace_all:
                return {
                    "error": (
                        f"old_string found {count} times in file. Provide more unique "
                        "surrounding context, or set replace_all=true to replace all instances."
                    )
                }

            if replace_all:
                updated_content = content.replace(old_string, new_string)
                replacements = count
            else:
                updated_content = content.replace(old_string, new_string, 1)
                replacements = 1

            p.write_text(updated_content, encoding="utf-8")
            return {
                "success": True,
                "path": str(p),
                "replacements": replacements,
                "bytes_written": len(updated_content.encode("utf-8")),
            }
        except PermissionError:
            return {"error": f"Permission denied: {path}"}
        except UnicodeDecodeError:
            return {"error": f"File is not a valid UTF-8 text file: {path}"}
        except Exception as e:
            return {"error": str(e)}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the local text file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text snippet to be replaced, including exact indentation",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "New replacement text",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "If true, replace all occurrences of old_string. Default is false.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        }
