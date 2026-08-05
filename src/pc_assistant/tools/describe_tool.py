from __future__ import annotations

from typing import Any

from pc_assistant.tools.base import ToolBase, tool
from pc_assistant.tools.registry import ToolRegistry


@tool(name="tool_help", description="Show a tool's actions and parameters.", skim_description="Full schema for one tool.")
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
                "available_tools": self._registry.list_llm_tools(),
            }

        tool = self._registry.get(tool_name)
        if tool is None:
            return {
                "error": f"Tool '{tool_name}' not found",
                "available_tools": self._registry.list_llm_tools(),
            }

        return {
            "tool": tool_name,
            "schema": self._registry.detailed_schema(tool_name),
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
            "description": "Return the model-facing name, operations, and parameters for a tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                },
                "required": ["tool_name"],
            },
        }
