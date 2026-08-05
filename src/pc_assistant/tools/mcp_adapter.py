"""MCP (Model Context Protocol) tool adapter.

Wraps tools exposed by an MCP server as ``ToolBase`` instances so they can be
registered in the agent's ``ToolRegistry`` alongside built-in tools.

Usage::

    adapter = MCPToolAdapter(server_url="http://localhost:3000/mcp")
    tools = await adapter.discover()
    for tool in tools:
        registry.register(tool)
"""
from __future__ import annotations

from typing import Any

import httpx

from pc_assistant.tools.base import ToolBase


class MCPTool(ToolBase):
    """A single tool backed by an MCP server endpoint."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        server_url: str,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.description = description
        self._input_schema = input_schema
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout

    async def execute(self, **kwargs: Any) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": self.name, "arguments": kwargs},
            "id": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._server_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("isError"):
                return {"error": result.get("content", [{}])[0].get("text", "MCP tool error")}
            content = result.get("content", [])
            if content and isinstance(content, list):
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(texts) if texts else result
            return result
        except httpx.HTTPError as e:
            return {"error": f"MCP request failed: {e}"}
        except Exception as e:
            return {"error": f"MCP error: {e}"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._input_schema,
        }


class MCPToolAdapter:
    """Discovers and wraps tools from an MCP-compatible server."""

    def __init__(self, server_url: str, timeout: float = 10.0) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout

    async def discover(self) -> list[MCPTool]:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            "id": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._server_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        tools_data = data.get("result", {}).get("tools", [])
        tools: list[MCPTool] = []
        for td in tools_data:
            tools.append(MCPTool(
                name=td.get("name", ""),
                description=td.get("description", ""),
                input_schema=td.get("inputSchema", {"type": "object", "properties": {}}),
                server_url=self._server_url,
                timeout=self._timeout,
            ))
        return tools
