from __future__ import annotations

import copy
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

    def resolve_name(self, name: str) -> str:
        """Resolve a name to its registered form (fallback tolerance)."""
        if name in self._tools:
            return name
        lower = name.lower().replace("-", "_")
        for registered in self._tools:
            if registered.lower().replace("-", "_") == lower:
                return registered
        return name

    def normalize_call(self, name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Map LLM output to internal name. No param aliasing needed anymore."""
        return self.resolve_name(name), dict(arguments)

    def all_schemas(self) -> list[dict[str, Any]]:
        """Wrapped skim schemas for LLM API injection."""
        return [
            {
                "type": "function",
                "function": tool.skim_schema(),
            }
            for tool in self._tools.values()
        ]

    def detailed_schema(self, name: str) -> dict[str, Any]:
        """Full schema plus examples for tool_help."""
        tool = self.get(name)
        if tool is None:
            return {}
        schema = tool.schema()
        examples = list(getattr(tool, "examples", []) or [])
        if not examples:
            properties = schema.get("parameters", {}).get("properties", {})
            required = set(schema.get("parameters", {}).get("required", []))
            example: dict[str, Any] = {}
            for key, prop in properties.items():
                if key not in required:
                    continue
                if prop.get("enum"):
                    example[key] = prop["enum"][0]
                elif prop.get("type") == "boolean":
                    example[key] = False
                elif prop.get("type") == "integer":
                    example[key] = 1
                elif prop.get("type") == "number":
                    example[key] = 1
                elif prop.get("type") == "array":
                    example[key] = []
                elif prop.get("type") == "object":
                    example[key] = {}
                else:
                    example[key] = "..."
            if example:
                examples = [example]
        return {
            "name": schema.get("name", tool.name),
            "description": schema.get("description", tool.description),
            "details": getattr(tool, "details", "") or tool.description,
            "parameters": schema.get("parameters", {}),
            "examples": examples,
        }

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def clear(self) -> None:
        self._tools.clear()

    async def _commit(self, internal_name: str, **kwargs: Any) -> Any:
        """Internal unchecked dispatch used only by VerifiedToolExecutor."""
        tool = self._tools.get(internal_name)
        if tool is None:
            raise ToolNotFoundError(internal_name)
        return await tool.execute(**kwargs)

    async def register_mcp_server(self, server_url: str) -> list[str]:
        """Discover and register all tools from an MCP server."""
        from pc_assistant.tools.mcp_adapter import MCPToolAdapter

        adapter = MCPToolAdapter(server_url)
        tools = await adapter.discover()
        names: list[str] = []
        for tool in tools:
            if tool.name:
                self._tools[tool.name] = tool
                names.append(tool.name)
        return names
