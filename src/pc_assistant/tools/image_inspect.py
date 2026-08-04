"""Tool exposing bounded visual perception to the main agent model."""
from __future__ import annotations

from typing import Any

from pc_assistant.context.scope import current_memory_scope
from pc_assistant.tools.base import ToolBase
from pc_assistant.vision.broker import VisionBroker


class ImageInspectTool(ToolBase):
    name = "image_inspect"
    description = (
        "Observe an available image by image_id. It describes visible content, reads text, "
        "locates visible items, or compares images; it does not diagnose or propose solutions."
    )
    is_side_effecting = False

    def __init__(self, broker: VisionBroker) -> None:
        self._broker = broker

    async def execute(self, **kwargs: Any) -> Any:
        image_id = str(kwargs.get("image_id", "")).strip()
        if not image_id:
            return {"error": "image_id is required"}
        try:
            return await self._broker.inspect(
                current_memory_scope().session_id,
                image_id,
                action=str(kwargs.get("action", "describe")),
                focus=str(kwargs.get("focus", "")),
                region=kwargs.get("region"),
                compare_image_id=str(kwargs.get("compare_image_id", "")).strip(),
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
                    "action": {
                        "type": "string",
                        "enum": ["describe", "ocr", "locate", "compare"],
                        "default": "describe",
                    },
                    "image_id": {"type": "string"},
                    "focus": {
                        "type": "string",
                        "description": "Visible detail to observe, such as 'the error dialog text'. Do not ask for advice or solutions.",
                    },
                    "region": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "width": {"type": "number"},
                            "height": {"type": "number"},
                        },
                        "required": ["x", "y", "width", "height"],
                        "additionalProperties": False,
                    },
                    "compare_image_id": {"type": "string"},
                },
                "required": ["image_id"],
                "additionalProperties": False,
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return self.schema()
