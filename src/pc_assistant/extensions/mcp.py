"""Official MCP HTTP/stdio adapters behind Knoa's ToolStep boundary."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from pc_assistant.extensions.manager import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionProvider,
)
from pc_assistant.extensions.models import MCPServerConfig, MCPToolPolicyConfig
from pc_assistant.tools.base import (
    ToolBase,
    ToolCapability,
)


_MAX_DISCOVERY_PAGES = 16
_MAX_DISCOVERED_TOOLS = 256
_MAX_RESULT_BYTES = 512_000
_MAX_TEXT_CHARS = 200_000
_PUBLIC_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_STDIO_BASE_ENV = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPClientPort(Protocol):
    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...

    async def close(self) -> None: ...


class StreamableHTTPMCPClient:
    """Own one official SDK transport and initialized ClientSession."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def start(self) -> None:
        if self._stack is not None:
            raise RuntimeError("MCP client is already started")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        try:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
            )
            read_stream, write_stream, _session_id = await stack.enter_async_context(
                streamable_http_client(
                    self._url,
                    http_client=http_client,
                )
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await asyncio.wait_for(session.initialize(), timeout=self._timeout)
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError("MCP client is not started")
        return self._session

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
        session = self._require_session()
        cursor: str | None = None
        definitions: list[MCPToolDefinition] = []
        for _page in range(_MAX_DISCOVERY_PAGES):
            result = await asyncio.wait_for(
                session.list_tools(cursor=cursor),
                timeout=self._timeout,
            )
            for tool in result.tools:
                definitions.append(
                    MCPToolDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=dict(tool.inputSchema),
                    )
                )
                if len(definitions) > _MAX_DISCOVERED_TOOLS:
                    raise ValueError("MCP server exposes too many tools")
            cursor = result.nextCursor
            if not cursor:
                return tuple(definitions)
        raise ValueError("MCP tool discovery pagination limit exceeded")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self._require_session()
        return await asyncio.wait_for(
            session.call_tool(name, arguments),
            timeout=self._timeout,
        )

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await stack.aclose()


class StdioMCPClient:
    """Own one locally supervised MCP child process over stdin/stdout."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._timeout = config.timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    def _environment(self) -> dict[str, str]:
        environment = {
            name: value
            for name in _STDIO_BASE_ENV
            if (value := os.environ.get(name)) is not None
        }
        for name in self._config.inherit_env:
            value = os.environ.get(name)
            if value is None:
                raise ValueError(f"Required MCP environment variable is not set: {name}")
            environment[name] = value
        return environment

    async def start(self) -> None:
        if self._stack is not None:
            raise RuntimeError("MCP stdio client is already started")
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=self._config.command,
                        args=list(self._config.args),
                        env=self._environment(),
                        cwd=self._config.working_directory or None,
                    )
                )
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await asyncio.wait_for(session.initialize(), timeout=self._timeout)
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError("MCP stdio client is not started")
        return self._session

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
        session = self._require_session()
        cursor: str | None = None
        definitions: list[MCPToolDefinition] = []
        for _page in range(_MAX_DISCOVERY_PAGES):
            result = await asyncio.wait_for(
                session.list_tools(cursor=cursor),
                timeout=self._timeout,
            )
            for tool in result.tools:
                definitions.append(
                    MCPToolDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=dict(tool.inputSchema),
                    )
                )
                if len(definitions) > _MAX_DISCOVERED_TOOLS:
                    raise ValueError("MCP server exposes too many tools")
            cursor = result.nextCursor
            if not cursor:
                return tuple(definitions)
        raise ValueError("MCP tool discovery pagination limit exceeded")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self._require_session()
        return await asyncio.wait_for(
            session.call_tool(name, arguments),
            timeout=self._timeout,
        )

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await stack.aclose()


def _public_tool_name(server_id: str, remote_name: str) -> str:
    normalized = _PUBLIC_NAME_UNSAFE.sub("_", remote_name).strip("_")
    if not normalized:
        raise ValueError("MCP tool name cannot be normalized safely")
    return f"mcp__{server_id}__{normalized}"


def _bounded_text(value: str, remaining: int) -> tuple[str, int, bool]:
    limit = max(0, min(_MAX_TEXT_CHARS, remaining))
    if len(value) <= limit:
        return value, remaining - len(value), False
    return value[:limit], 0, True


def _render_mcp_result(result: Any) -> dict[str, Any]:
    remaining = _MAX_TEXT_CHARS
    content: list[dict[str, Any]] = []
    error_texts: list[str] = []
    for block in tuple(getattr(result, "content", ())):
        block_type = str(getattr(block, "type", "unknown"))
        if block_type == "text":
            text, remaining, truncated = _bounded_text(
                str(getattr(block, "text", "")),
                remaining,
            )
            rendered = {"type": "text", "text": text}
            if truncated:
                rendered["truncated"] = True
            content.append(rendered)
            if text:
                error_texts.append(text)
        elif block_type in {"image", "audio"}:
            data = str(getattr(block, "data", ""))
            content.append(
                {
                    "type": block_type,
                    "media_type": str(getattr(block, "mimeType", "")),
                    "encoded_size": len(data),
                    "data_omitted": True,
                }
            )
        else:
            dumped = block.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(dumped, dict):
                resource = dumped.get("resource")
                if isinstance(resource, dict) and "blob" in resource:
                    resource["encoded_size"] = len(str(resource.pop("blob")))
                    resource["data_omitted"] = True
                content.append(dumped)

    if bool(getattr(result, "isError", False)):
        detail = "\n".join(error_texts).strip()
        return {"error": detail[:2000] or "MCP tool reported an error"}

    output: dict[str, Any] = {"content": content}
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        output["structured_content"] = structured
    encoded = json.dumps(output, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > _MAX_RESULT_BYTES:
        return {"error": "MCP tool result exceeds the configured size limit"}
    return output


class MCPTool(ToolBase):
    def __init__(
        self,
        *,
        public_name: str,
        remote_name: str,
        description: str,
        input_schema: dict[str, Any],
        policy: MCPToolPolicyConfig,
        client: MCPClientPort,
    ) -> None:
        self.name = public_name
        self.description = description or f"MCP tool {remote_name}"
        self.effect = policy.effect
        self.capabilities = frozenset(
            {
                *policy.capabilities,
                ToolCapability.MCP,
            }
        )
        self.risk = policy.risk
        self._remote_name = remote_name
        self._input_schema = input_schema
        self._client = client

    async def execute(self, **kwargs: Any) -> Any:
        try:
            result = await self._client.call_tool(self._remote_name, kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"error": "MCP tool call failed"}
        return _render_mcp_result(result)

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self._input_schema,
        }


class MCPServerProvider(ExtensionProvider):
    def __init__(
        self,
        server_id: str,
        config: MCPServerConfig | None = None,
        *,
        config_loader: Callable[[], MCPServerConfig] | None = None,
        client_factory: Callable[[MCPServerConfig], MCPClientPort] | None = None,
    ) -> None:
        if (config is None) == (config_loader is None):
            raise ValueError("MCP provider requires exactly one configuration source")
        self._server_id = server_id
        self._config = config
        self._config_loader = config_loader
        self._client_factory = client_factory
        self._descriptor = ExtensionDescriptor(
            extension_id=f"mcp:{server_id}",
            kind=ExtensionKind.MCP,
        )
        self._client: MCPClientPort | None = None

    @property
    def descriptor(self) -> ExtensionDescriptor:
        return self._descriptor

    async def start(self) -> tuple[ToolBase, ...]:
        if self._config is not None:
            config = self._config
        else:
            loader = self._config_loader
            if loader is None:  # guarded by __init__; keeps the boundary explicit
                raise RuntimeError("MCP configuration loader is unavailable")
            config = loader()
        if not config.enabled:
            raise ValueError("MCP server is disabled")
        if self._client_factory is not None:
            client = self._client_factory(config)
        elif config.transport == "stdio":
            client = StdioMCPClient(config)
        else:
            client = StreamableHTTPMCPClient(
                config.url,
                timeout_seconds=config.timeout_seconds,
            )
        self._client = client
        await client.start()
        definitions = {
            definition.name: definition
            for definition in await client.list_tools()
        }
        tools: list[ToolBase] = []
        for remote_name, policy in config.tools.items():
            definition = definitions.get(remote_name)
            if definition is None:
                continue
            tools.append(
                MCPTool(
                    public_name=_public_tool_name(self._server_id, remote_name),
                    remote_name=remote_name,
                    description=definition.description,
                    input_schema=definition.input_schema,
                    policy=policy,
                    client=client,
                )
            )
        return tuple(tools)

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()


def build_mcp_providers(
    configs: dict[str, MCPServerConfig],
) -> tuple[MCPServerProvider, ...]:
    return tuple(
        MCPServerProvider(server_id, config)
        for server_id, config in configs.items()
        if config.enabled
    )
