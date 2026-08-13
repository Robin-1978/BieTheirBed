from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from knoa_platform.tools.base import (
    ToolBase,
    ToolCapability,
    ToolEffect,
    ToolRisk,
)
from knoa_platform.tools.registry import ToolRegistry


class DescribeTool(ToolBase):
    """Model-facing discovery over the complete authorized tool registry."""
    name = "tool_help"
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset(ToolCapability)
    risk = ToolRisk.LOW
    description = (
        "Search available tools, or show the full schema and examples for an "
        "exact tool name."
    )

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, **kwargs: Any) -> Any:
        tool_name = str(kwargs.get("tool_name") or "").strip()
        query = str(kwargs.get("query") or "").strip()
        if not tool_name:
            matches = self._matches(query)
            return {
                "found": False,
                "query": query,
                "matches": matches,
                "available_tools": (
                    self._registry.list_tools()
                    if not query
                    else [match["name"] for match in matches]
                ),
            }

        tool = self._registry.get(tool_name)
        if tool is None:
            available_tools = self._registry.list_tools()
            return {
                "found": False,
                "query": tool_name,
                "suggestions": get_close_matches(
                    tool_name,
                    available_tools,
                    n=3,
                    cutoff=0.35,
                ),
                "available_tools": available_tools,
            }

        return {
            "found": True,
            "tool": tool_name,
            "schema": self._registry.detailed_schema(tool_name),
            "description": tool.description,
        }

    def _matches(self, query: str) -> list[dict[str, str]]:
        available = self._registry.list_tools()
        if not query:
            names = available[:100]
        else:
            normalized = query.casefold()
            direct = []
            for name in available:
                tool = self._registry.get(name)
                if tool is None:
                    continue
                searchable = f"{name} {tool.description}".casefold()
                if normalized in searchable or all(
                    token in searchable for token in normalized.split()
                ):
                    direct.append(name)
            fuzzy = get_close_matches(query, available, n=12, cutoff=0.25)
            names = list(dict.fromkeys([*direct, *fuzzy]))[:12]
        return [
            {
                "name": name,
                "description": self._registry.get(name).description,
            }
            for name in names
            if self._registry.get(name) is not None
        ]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "Exact name of the tool to describe",
                    },
                    "query": {
                        "type": "string",
                        "description": "Words to search in tool names and descriptions",
                    },
                },
                "additionalProperties": False,
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
                    "query": {"type": "string"},
                },
            },
        }
