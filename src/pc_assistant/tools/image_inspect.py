"""Tool exposing bounded visual perception to the main agent model."""
from __future__ import annotations

from typing import Any

from pc_assistant.context.scope import current_memory_scope
from pc_assistant.tools.base import ToolBase
from pc_assistant.vision.broker import VisionBroker


class ImageInspectTool(ToolBase):
    name = "image_inspect"
    description = (
        "Ask the local vision model one question about visible content in an available image. "
        "The main model must provide both image_id and a question derived from the user's request. "
        "The question may ask for description, visible text, location, or comparison of visible "
        "evidence, but must not ask for diagnosis or solutions."
    )
    is_side_effecting = False

    def __init__(self, broker: VisionBroker) -> None:
        self._broker = broker

    async def execute(self, **kwargs: Any) -> Any:
        image_id = str(kwargs.get("image_id", "")).strip()
        if not image_id:
            return {"error": "image_id is required"}
        question = str(kwargs.get("question", "")).strip()
        if not question:
            return {"error": "question is required and must be supplied by the main model"}
        try:
            return await self._broker.inspect(
                current_memory_scope().session_id,
                image_id,
                question=question,
            )
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"error": str(exc), "image_id": image_id}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string"},
                    "question": {
                        "type": "string",
                        "description": (
                            "A visual question dynamically written by the main model from the "
                            "user's current request and conversation context. "
                            "Do not ask for diagnosis, recommendations, or solutions."
                        ),
                    },
                },
                "required": ["image_id", "question"],
                "additionalProperties": False,
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return self.schema()
