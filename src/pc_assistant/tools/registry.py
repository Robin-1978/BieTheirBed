from __future__ import annotations

from typing import Any, TYPE_CHECKING

from pc_assistant.tools.base import ToolBase

if TYPE_CHECKING:
    from pc_assistant.harness.safety import SafetyChecker


class ToolRegistry:
    def __init__(self, safety: SafetyChecker | None = None) -> None:
        self._tools: dict[str, ToolBase] = {}
        self._safety = safety

    def register(self, tool: ToolBase) -> None:
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolBase | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def all_schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            raw = tool.schema()
            schemas.append({
                "type": "function",
                "function": {
                    "name": raw["name"],
                    "description": raw.get("description", ""),
                    "parameters": raw.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return schemas

    async def execute(self, tool_name: str, **kwargs: Any) -> Any:
        """Execute a tool by name.

        Args:
            tool_name: The name of the tool to execute
            **kwargs: Arguments to pass to the tool
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Tool '{tool_name}' not found in registry")
        if self._safety is not None:
            result = self._safety.check_tool_call(tool_name, kwargs)
            if not result:
                return {"error": f"Blocked by safety check: {result.reason}"}
        return await tool.execute(**kwargs)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
