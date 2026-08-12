"""Official MCP HTTP/stdio adapters behind Knoa's ToolStep boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pc_assistant.extensions.manager import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionProvider,
)
from pc_assistant.extensions.models import MCPServerConfig, MCPToolPolicyConfig
from pc_assistant.tools.base import ToolBase, ToolCapability

_MAX_DISCOVERY_PAGES = 16
_MAX_DISCOVERED_TOOLS = 256
_MAX_DISCOVERED_PROMPTS = 256
_MAX_DISCOVERED_RESOURCES = 1024
_MAX_RESULT_BYTES = 512_000
_MAX_TEXT_CHARS = 200_000
_MAX_RESOURCE_TEXT_CHARS = 128_000
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


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None


@dataclass(frozen=True)
class MCPPromptDefinition:
    name: str
    description: str


@dataclass(frozen=True)
class MCPResourceCapabilities:
    available: bool = False
    subscribe: bool = False
    list_changed: bool = False


@dataclass(frozen=True)
class MCPResourceDefinition:
    uri: str
    name: str
    description: str
    mime_type: str


@dataclass(frozen=True)
class MCPResourceContent:
    uri: str
    mime_type: str
    text: str = ""
    encoded_size: int = 0


@dataclass(frozen=True)
class MCPResourceSnapshot:
    contents: tuple[MCPResourceContent, ...]


@dataclass(frozen=True)
class MCPResourceNotification:
    kind: Literal["list_changed", "updated"]
    uri: str = ""


MCPNotificationHandler = Callable[[MCPResourceNotification], Awaitable[None]]


class MCPClientPort(Protocol):
    def set_notification_handler(
        self,
        handler: MCPNotificationHandler | None,
    ) -> None: ...

    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]: ...

    async def list_prompts(self) -> tuple[MCPPromptDefinition, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...

    def resource_capabilities(self) -> MCPResourceCapabilities: ...

    async def list_resources(self) -> tuple[MCPResourceDefinition, ...]: ...

    async def read_resource(self, uri: str) -> MCPResourceSnapshot: ...

    async def subscribe_resource(self, uri: str) -> None: ...

    async def unsubscribe_resource(self, uri: str) -> None: ...

    async def close(self) -> None: ...


def _attribute(value: Any, snake_name: str, camel_name: str) -> Any:
    return getattr(value, snake_name, getattr(value, camel_name, None))


def _resource_capabilities(
    handshake_result: Any,
    *,
    modern: bool,
) -> MCPResourceCapabilities:
    capabilities = getattr(handshake_result, "capabilities", None)
    resources = getattr(capabilities, "resources", None)
    if resources is None:
        return MCPResourceCapabilities()
    return MCPResourceCapabilities(
        available=True,
        subscribe=modern or bool(getattr(resources, "subscribe", False)),
        list_changed=bool(_attribute(resources, "list_changed", "listChanged")),
    )


def _resource_definitions(result: Any) -> tuple[MCPResourceDefinition, ...]:
    definitions: list[MCPResourceDefinition] = []
    for resource in tuple(getattr(result, "resources", ())):
        definitions.append(
            MCPResourceDefinition(
                uri=str(resource.uri),
                name=str(resource.name),
                description=str(getattr(resource, "description", "") or ""),
                mime_type=str(_attribute(resource, "mime_type", "mimeType") or ""),
            )
        )
    return tuple(definitions)


def _resource_snapshot(result: Any) -> MCPResourceSnapshot:
    remaining = _MAX_RESOURCE_TEXT_CHARS
    contents: list[MCPResourceContent] = []
    for content in tuple(getattr(result, "contents", ())):
        uri = str(getattr(content, "uri", ""))
        mime_type = str(_attribute(content, "mime_type", "mimeType") or "")
        text_value = getattr(content, "text", None)
        if text_value is not None:
            text = str(text_value)
            if len(text) > remaining:
                raise ValueError("MCP Resource text exceeds the configured size limit")
            remaining -= len(text)
            contents.append(
                MCPResourceContent(
                    uri=uri,
                    mime_type=mime_type or "text/plain",
                    text=text,
                )
            )
            continue
        blob = str(getattr(content, "blob", "") or "")
        contents.append(
            MCPResourceContent(
                uri=uri,
                mime_type=mime_type or "application/octet-stream",
                encoded_size=len(blob),
            )
        )
    return MCPResourceSnapshot(contents=tuple(contents))


async def _dispatch_resource_notification(
    handler: MCPNotificationHandler | None,
    message: Any,
) -> None:
    if handler is None:
        return
    try:
        from mcp import types

        if not isinstance(message, types.ServerNotification):
            return
        match message.root:
            case types.ResourceListChangedNotification():
                notification = MCPResourceNotification(kind="list_changed")
            case types.ResourceUpdatedNotification(params=params):
                notification = MCPResourceNotification(
                    kind="updated",
                    uri=str(params.uri),
                )
            case _:
                return
        await handler(notification)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("MCP Resource notification handler failed")


async def _dispatch_subscription_event(
    handler: MCPNotificationHandler | None,
    event: Any,
) -> None:
    if handler is None:
        return
    from mcp.shared.subscriptions import ResourcesListChanged, ResourceUpdated

    if isinstance(event, ResourcesListChanged):
        notification = MCPResourceNotification(kind="list_changed")
    elif isinstance(event, ResourceUpdated):
        notification = MCPResourceNotification(kind="updated", uri=event.uri)
    else:
        return
    try:
        await handler(notification)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("MCP subscription event handler failed")


async def _negotiate_session(session: Any, timeout: float) -> tuple[Any, bool]:
    """Prefer MCP 2026 discovery and fall back only for a legacy server."""

    from mcp import types
    from mcp.shared.exceptions import MCPError

    try:
        result = await asyncio.wait_for(session.discover(), timeout=timeout)
        return result, True
    except MCPError as exc:
        if exc.code != types.METHOD_NOT_FOUND:
            raise
    result = await asyncio.wait_for(session.initialize(), timeout=timeout)
    return result, False


class _SessionClientMixin:
    _timeout: float
    _session: Any
    _notification_handler: MCPNotificationHandler | None
    _resource_capabilities: MCPResourceCapabilities
    _modern: bool
    _resource_subscriptions: set[str]
    _listen_task: asyncio.Task[None] | None

    async def _finish_start(self, session: Any) -> None:
        handshake, self._modern = await _negotiate_session(session, self._timeout)
        self._session = session
        self._resource_capabilities = _resource_capabilities(
            handshake,
            modern=self._modern,
        )

    async def _handle_message(self, message: Any) -> None:
        if not self._modern:
            await _dispatch_resource_notification(self._notification_handler, message)

    async def _modern_listener(
        self,
        subscriptions: tuple[str, ...],
        ready: asyncio.Future[None],
    ) -> None:
        from mcp.client.subscriptions import listen

        try:
            async with listen(
                self._require_session(),
                resources_list_changed=self._resource_capabilities.list_changed,
                resource_subscriptions=subscriptions,
            ) as events:
                if not ready.done():
                    ready.set_result(None)
                async for event in events:
                    await _dispatch_subscription_event(
                        self._notification_handler,
                        event,
                    )
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - isolate a remote MCP stream
            if not ready.done():
                ready.set_exception(exc)
            else:
                logger.warning("MCP subscriptions/listen ended: %s", exc)

    async def _restart_modern_listener(self) -> None:
        task, self._listen_task = self._listen_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not self._modern or self._session is None:
            return
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(
            self._modern_listener(tuple(sorted(self._resource_subscriptions)), ready),
            name="mcp-subscriptions-listen",
        )
        self._listen_task = task
        await asyncio.wait_for(ready, timeout=self._timeout)

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
        from mcp import types

        session = self._require_session()
        cursor: str | None = None
        definitions: list[MCPToolDefinition] = []
        for _page in range(_MAX_DISCOVERY_PAGES):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await asyncio.wait_for(
                session.list_tools(params=params),
                timeout=self._timeout,
            )
            for tool in result.tools:
                annotations = getattr(tool, "annotations", None)
                definitions.append(
                    MCPToolDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=dict(
                            _attribute(tool, "input_schema", "inputSchema")
                        ),
                        read_only_hint=_attribute(
                            annotations, "read_only_hint", "readOnlyHint"
                        ),
                        destructive_hint=_attribute(
                            annotations, "destructive_hint", "destructiveHint"
                        ),
                        idempotent_hint=_attribute(
                            annotations, "idempotent_hint", "idempotentHint"
                        ),
                        open_world_hint=_attribute(
                            annotations, "open_world_hint", "openWorldHint"
                        ),
                    )
                )
                if len(definitions) > _MAX_DISCOVERED_TOOLS:
                    raise ValueError("MCP server exposes too many tools")
            cursor = _attribute(result, "next_cursor", "nextCursor")
            if not cursor:
                return tuple(definitions)
        raise ValueError("MCP tool discovery pagination limit exceeded")

    async def list_prompts(self) -> tuple[MCPPromptDefinition, ...]:
        from mcp import types

        session = self._require_session()
        cursor: str | None = None
        definitions: list[MCPPromptDefinition] = []
        for _page in range(_MAX_DISCOVERY_PAGES):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await asyncio.wait_for(
                session.list_prompts(params=params),
                timeout=self._timeout,
            )
            definitions.extend(
                MCPPromptDefinition(
                    name=prompt.name,
                    description=prompt.description or "",
                )
                for prompt in result.prompts
            )
            if len(definitions) > _MAX_DISCOVERED_PROMPTS:
                raise ValueError("MCP server exposes too many prompts")
            cursor = _attribute(result, "next_cursor", "nextCursor")
            if not cursor:
                return tuple(definitions)
        raise ValueError("MCP prompt discovery pagination limit exceeded")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await asyncio.wait_for(
            self._require_session().call_tool(name, arguments),
            timeout=self._timeout,
        )

    def resource_capabilities(self) -> MCPResourceCapabilities:
        return self._resource_capabilities

    async def list_resources(self) -> tuple[MCPResourceDefinition, ...]:
        from mcp import types

        session = self._require_session()
        cursor: str | None = None
        definitions: list[MCPResourceDefinition] = []
        seen_cursors: set[str] = set()
        for _page in range(_MAX_DISCOVERY_PAGES):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await asyncio.wait_for(
                session.list_resources(params=params),
                timeout=self._timeout,
            )
            definitions.extend(_resource_definitions(result))
            if len(definitions) > _MAX_DISCOVERED_RESOURCES:
                raise ValueError("MCP server exposes too many resources")
            cursor = _attribute(result, "next_cursor", "nextCursor")
            if not cursor:
                return tuple(definitions)
            if cursor in seen_cursors:
                raise ValueError("MCP Resource pagination repeated a cursor")
            seen_cursors.add(cursor)
        raise ValueError("MCP Resource discovery pagination limit exceeded")

    async def read_resource(self, uri: str) -> MCPResourceSnapshot:
        result = await asyncio.wait_for(
            self._require_session().read_resource(uri),
            timeout=self._timeout,
        )
        return _resource_snapshot(result)

    async def subscribe_resource(self, uri: str) -> None:
        if self._modern:
            if uri not in self._resource_subscriptions:
                self._resource_subscriptions.add(uri)
                await self._restart_modern_listener()
            return
        await asyncio.wait_for(
            self._require_session().subscribe_resource(uri),
            timeout=self._timeout,
        )

    async def unsubscribe_resource(self, uri: str) -> None:
        if self._modern:
            if uri in self._resource_subscriptions:
                self._resource_subscriptions.remove(uri)
                await self._restart_modern_listener()
            return
        await asyncio.wait_for(
            self._require_session().unsubscribe_resource(uri),
            timeout=self._timeout,
        )

    async def _stop_listener(self) -> None:
        task, self._listen_task = self._listen_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._resource_subscriptions.clear()


class StreamableHTTPMCPClient(_SessionClientMixin):
    """Own one official SDK transport and initialized ClientSession."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._notification_handler: MCPNotificationHandler | None = None
        self._resource_capabilities = MCPResourceCapabilities()
        self._modern = False
        self._resource_subscriptions: set[str] = set()
        self._listen_task: asyncio.Task[None] | None = None

    def set_notification_handler(
        self,
        handler: MCPNotificationHandler | None,
    ) -> None:
        if self._stack is not None:
            raise RuntimeError("MCP notification handler must be set before start")
        self._notification_handler = handler

    async def start(self) -> None:
        if self._stack is not None:
            raise RuntimeError("MCP client is already started")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(self._url)
            )
            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._timeout,
                    message_handler=self._handle_message,
                )
            )
            await self._finish_start(session)
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError("MCP client is not started")
        return self._session

    async def close(self) -> None:
        await self._stop_listener()
        stack, self._stack = self._stack, None
        self._session = None
        self._resource_capabilities = MCPResourceCapabilities()
        if stack is not None:
            await stack.aclose()


class StdioMCPClient(_SessionClientMixin):
    """Own one locally supervised MCP child process over stdin/stdout."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._timeout = config.timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._notification_handler: MCPNotificationHandler | None = None
        self._resource_capabilities = MCPResourceCapabilities()
        self._modern = False
        self._resource_subscriptions: set[str] = set()
        self._listen_task: asyncio.Task[None] | None = None

    def set_notification_handler(
        self,
        handler: MCPNotificationHandler | None,
    ) -> None:
        if self._stack is not None:
            raise RuntimeError("MCP notification handler must be set before start")
        self._notification_handler = handler

    def _environment(self) -> dict[str, str]:
        environment = {
            name: value
            for name in _STDIO_BASE_ENV
            if (value := os.environ.get(name)) is not None
        }
        for name in self._config.inherit_env:
            value = os.environ.get(name)
            if value is None:
                raise ValueError(
                    f"Required MCP environment variable is not set: {name}"
                )
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
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self._timeout,
                    message_handler=self._handle_message,
                )
            )
            await self._finish_start(session)
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError("MCP stdio client is not started")
        return self._session

    async def close(self) -> None:
        await self._stop_listener()
        stack, self._stack = self._stack, None
        self._session = None
        self._resource_capabilities = MCPResourceCapabilities()
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
                    "media_type": str(_attribute(block, "mime_type", "mimeType") or ""),
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

    if bool(_attribute(result, "is_error", "isError")):
        detail = "\n".join(error_texts).strip()
        return {"error": detail[:2000] or "MCP tool reported an error"}

    output: dict[str, Any] = {"content": content}
    structured = _attribute(result, "structured_content", "structuredContent")
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
        except Exception:  # noqa: BLE001 - remote provider failures become tool results
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
        self._notification_listeners: list[MCPNotificationHandler] = []

    @property
    def descriptor(self) -> ExtensionDescriptor:
        return self._descriptor

    @property
    def server_id(self) -> str:
        return self._server_id

    @property
    def config(self) -> MCPServerConfig | None:
        return self._config

    def add_notification_listener(self, listener: MCPNotificationHandler) -> None:
        self._notification_listeners.append(listener)

    async def _dispatch_notification(
        self,
        notification: MCPResourceNotification,
    ) -> None:
        for listener in tuple(self._notification_listeners):
            await listener(notification)

    def _require_client(self) -> MCPClientPort:
        if self._client is None:
            raise RuntimeError("MCP provider is not running")
        return self._client

    def resource_capabilities(self) -> MCPResourceCapabilities:
        return self._require_client().resource_capabilities()

    async def list_resources(self) -> tuple[MCPResourceDefinition, ...]:
        return await self._require_client().list_resources()

    async def list_prompts(self) -> tuple[MCPPromptDefinition, ...]:
        return await self._require_client().list_prompts()

    async def read_resource(self, uri: str) -> MCPResourceSnapshot:
        return await self._require_client().read_resource(uri)

    async def subscribe_resource(self, uri: str) -> None:
        await self._require_client().subscribe_resource(uri)

    async def unsubscribe_resource(self, uri: str) -> None:
        await self._require_client().unsubscribe_resource(uri)

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
        client = (
            self._client_factory(config)
            if self._client_factory is not None
            else create_mcp_client(config)
        )
        self._client = client
        client.set_notification_handler(self._dispatch_notification)
        await client.start()
        definitions = {
            definition.name: definition for definition in await client.list_tools()
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


def create_mcp_client(config: MCPServerConfig) -> MCPClientPort:
    if config.transport == "stdio":
        return StdioMCPClient(config)
    return StreamableHTTPMCPClient(
        config.url,
        timeout_seconds=config.timeout_seconds,
    )
