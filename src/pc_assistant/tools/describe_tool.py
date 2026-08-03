from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.registry import ToolRegistry


class DescribeTool(ToolBase):
    """Meta-tool to query the full schema of any registered tool."""
    name = "describe_tool"
    description = "Get the complete JSON schema and documentation for any available tool."

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
            "schema": tool.schema(),
            "description": tool.description,
        }

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
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

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Query full schema for any tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                },
                "required": ["tool_name"],
            },
        }