"""Prepare an existing local file as a user-deliverable artifact."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.scope import current_memory_scope
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class ArtifactPrepareTool(ToolBase):
    name = "attach"
    description = (
        "Deliver an existing local file, including files in Desktop or Downloads. "
        "The source file is borrowed read-only and is never copied, modified, or deleted."
    )
    effect = ToolEffect.EXTERNAL_SIDE_EFFECT
    capabilities = frozenset({ToolCapability.HOST_READ})
    risk = ToolRisk.HIGH

    def __init__(self, store: ArtifactStore, working_directory: str = ".") -> None:
        self._store = store
        self._working_directory = Path(working_directory).expanduser().resolve()

    async def execute(self, **kwargs: Any) -> Any:
        path = str(kwargs.get("path", "")).strip()
        if not path:
            return {"error": "path is required"}
        try:
            candidate = Path(path).expanduser()
            resolved = candidate.resolve() if candidate.is_absolute() else (
                self._working_directory / candidate
            ).resolve()
            artifact = self._store.prepare_path(current_memory_scope().session_id, resolved)
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
        return {"success": True, "artifact": artifact}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path of an existing local file to deliver to the current user; "
                            "Desktop and Downloads paths are allowed."
                        ),
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
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
                },
                "required": ["path"],
            },
        }
