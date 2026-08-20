"""Perception-only bridge from a text main model to the dedicated vision model."""
from __future__ import annotations

from typing import Any

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import current_tool_step_context
from knoa_platform.tools.base import ToolBase, ToolEffect, ToolPolicy, ToolRisk
from knoa_platform.vision import VisionBroker


class ImageInspectTool(ToolBase):
    name = "image_inspect"
    description = (
        "Observe an attached image by artifact_id using the dedicated vision model. "
        "Ask only about visible content or text; diagnosis and solutions remain with the main agent."
    )
    effect = ToolEffect.READ_ONLY
    risk = ToolRisk.LOW

    def __init__(self, broker: VisionBroker) -> None:
        self._broker = broker

    @property
    def policy(self) -> ToolPolicy:
        return ToolPolicy(
            effect=self.effect if self._broker.available else ToolEffect.UNKNOWN,
            capabilities=self.capabilities,
            risk=self.risk,
        )

    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("image_inspect requires an invocation scope")

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        artifact_id = str(kwargs.get("artifact_id", "")).strip()
        question = str(kwargs.get("question", "")).strip()
        if not artifact_id:
            return {"error": "artifact_id is required"}
        if not question:
            return {"error": "question is required"}
        context = current_tool_step_context()
        try:
            return await self._broker.inspect(
                scope.session_handle,
                artifact_id,
                question=question,
                cancellation=context.cancellation if context is not None else None,
            )
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            return {"error": str(exc), "artifact_id": artifact_id}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                        "description": "A question about visible evidence only, derived from the user's request.",
                    },
                },
                "required": ["artifact_id", "question"],
                "additionalProperties": False,
            },
        }
