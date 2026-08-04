from __future__ import annotations

from typing import Any

from pc_assistant.exceptions import ToolNotFoundError
from pc_assistant.tools.base import ToolBase


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolBase] = {}

    def register(self, tool: ToolBase) -> None:
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolBase | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def all_schemas(self) -> list[dict[str, Any]]:
        """Wrapped tool schemas for LLM API injection (single source: ``core_schema``)."""
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            raw = tool.core_schema()
            schemas.append({
                "type": "function",
                "function": {
                    "name": raw["name"],
                    "description": raw.get("description", ""),
                    "parameters": raw.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        return schemas

    def clear(self) -> None:
        """Unregister all tools."""
        self._tools.clear()

    async def execute(self, tool_name: str, **kwargs: Any) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(tool_name)
        return await tool.execute(**kwargs)

    async def register_mcp_server(self, server_url: str) -> list[str]:
        """Discover and register all tools from an MCP server. Returns registered tool names."""
        from pc_assistant.tools.mcp_adapter import MCPToolAdapter

        adapter = MCPToolAdapter(server_url)
        tools = await adapter.discover()
        names: list[str] = []
        for tool in tools:
            if tool.name:
                self._tools[tool.name] = tool
                names.append(tool.name)
        return names

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
