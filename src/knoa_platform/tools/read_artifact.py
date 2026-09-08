"""Read bounded text from an artifact owned by the current session."""
from __future__ import annotations

from typing import Any

from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.scope import current_memory_scope
from knoa_platform.tools.base import ToolBase, ToolEffect, ToolRisk


class ReadArtifactTool(ToolBase):
    name = "read_artifact"
    description = (
        "Read text content from an attached file or tool result artifact. "
        "Supports offset (1-based start line) and limit (line count) for pagination. "
        "Inspect returned # showing, # has_more, and # next_offset headers; stop probing when has_more is False."
    )
    effect = ToolEffect.READ_ONLY
    risk = ToolRisk.LOW

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def execute(self, **kwargs: Any) -> Any:
        artifact_id = str(kwargs.get("artifact_id", "")).strip()
        if not artifact_id:
            return {"error": "artifact_id is required"}
        offset = kwargs.get("offset")
        limit = kwargs.get("limit")
        try:
            return self._store.read_text(
                current_memory_scope().session_id,
                artifact_id,
                offset=int(offset) if offset is not None else None,
                limit=int(limit) if limit is not None else None,
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
                        "description": "Artifact identifier to read",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based starting line number to read",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Number of lines to read (default 100, max 500)",
                    },
                },
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
        }
