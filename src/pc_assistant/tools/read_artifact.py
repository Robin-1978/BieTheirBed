"""Read bounded text from an artifact owned by the current session."""
from __future__ import annotations

from typing import Any

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import current_memory_scope
from pc_assistant.tools.base import ToolBase, ToolEffect, ToolRisk


class ReadArtifactTool(ToolBase):
    name = "read_artifact"
    description = (
        "Read text content from an attached file by artifact ID. "
        "Use the artifact ID shown in the user message."
    )
    effect = ToolEffect.READ_ONLY
    risk = ToolRisk.LOW

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def execute(self, **kwargs: Any) -> Any:
        artifact_id = str(kwargs.get("artifact_id", "")).strip()
        if not artifact_id:
            return {"error": "artifact_id is required"}
        try:
            return self._store.read_text(
                current_memory_scope().session_id,
                artifact_id,
            )
        except (KeyError, OSError, ValueError) as exc:
            return {"error": str(exc)}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    }
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
        }
