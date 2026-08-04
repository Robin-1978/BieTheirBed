"""Prepare an existing local file as a user-deliverable artifact."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import current_memory_scope
from pc_assistant.tools.base import ToolBase


class ArtifactPrepareTool(ToolBase):
    name = "artifact_prepare"
    description = (
        "Borrow an existing local file or image for delivery to the current user. "
        "The source is not copied, modified, or deleted. This does not open the file "
        "locally; it emits a client-deliverable artifact."
    )
    # Preparing this artifact causes the active client to disclose a copy to
    # the current conversation, so it belongs on the verified side-effect
    # path even though this tool doesn't know anything about the client.
    is_side_effecting = True

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

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the existing file to deliver to the current user.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return self.schema()
