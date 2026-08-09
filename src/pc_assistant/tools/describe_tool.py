from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolRisk,
)
from pc_assistant.tools.registry import ToolRegistry


class DescribeTool(ToolBase):
    """Meta-tool to query the full schema of any registered tool."""
    name = "tool_help"
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset(ToolCapability)
    risk = ToolRisk.LOW
    description = "Show full schema and examples for a tool."

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, **kwargs: Any) -> Any:
        tool_name = kwargs.get("tool_name", "").strip()
        if not tool_name:
            return {
                "error": "tool_name is required",
                "available_tools": self._registry.list_tools(),
            }

        tool = self._registry.get(tool_name)
        if tool is None:
            return {
                "error": f"Tool '{tool_name}' not found",
                "available_tools": self._registry.list_tools(),
            }

        return {
            "tool": tool_name,
            "schema": self._registry.detailed_schema(tool_name),
            "description": tool.description,
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool to describe",
                    },
                },
                "required": ["tool_name"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                },
                "required": ["tool_name"],
            },
        }
