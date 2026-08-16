"""Official MCP HTTP/stdio adapters behind Knoa's ToolStep boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from knoa_platform.extensions.manager import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionProvider,
)
from knoa_platform.extensions.models import MCPServerConfig, MCPToolPolicyConfig
from knoa_platform.tools.base import ToolBase, ToolCapability

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
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TMPDIR",
    }
)


logger = logging.getLogger(__name__)


def _schema_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    fields: list[dict[str, Any]] = []
    for field_id, value in properties.items():
        if not isinstance(field_id, str) or not isinstance(value, dict):
            continue
        field: dict[str, Any] = {
            "id": field_id,
            "title": str(value.get("title") or field_id),
            "description": str(value.get("description") or ""),
        }
        enum = value.get("enum")
        if isinstance(enum, list):
            field["options"] = [
                {"value": item, "label": str(item)}
                for item in enum
                if isinstance(item, (str, int, float, bool))
            ]
        fields.append(field)
    return fields


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


def mcp_inventory_digest(
    tools: tuple[MCPToolDefinition, ...],
    resources: tuple[MCPResourceDefinition, ...] = (),
    prompts: tuple[MCPPromptDefinition, ...] = (),
) -> str:
    """Canonical inventory fingerprint used to fail closed on remote drift."""

    payload = {
        "tools": [
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.input_schema,
                "read_only": item.read_only_hint,
                "destructive": item.destructive_hint,
                "idempotent": item.idempotent_hint,
                "open_world": item.open_world_hint,
            }
            for item in sorted(tools, key=lambda value: value.name)
        ],
        "resources": [
            {"uri": item.uri, "name": item.name, "mime_type": item.mime_type}
            for item in sorted(resources, key=lambda value: value.uri)
        ],
        "prompts": [
            {"name": item.name, "description": item.description}
            for item in sorted(prompts, key=lambda value: value.name)
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
MCPElicitationHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]


class MCPClientPort(Protocol):
    def set_notification_handler(
        self,
        handler: MCPNotificationHandler | None,
    ) -> None: ...

    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]: ...

    async def list_prompts(self) -> tuple[MCPPromptDefinition, ...]: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        elicitation_handler: MCPElicitationHandler | None = None,
    ) -> Any: ...

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
    _elicitation_handler: MCPElicitationHandler | None
    _tool_call_lock: asyncio.Lock

    async def _elicit(self, _context: Any, params: Any) -> Any:
        from mcp import types

        if getattr(params, "mode", "form") != "form":
            return types.ElicitResult(action="decline")
        handler = self._elicitation_handler
        if handler is None:
            return types.ElicitResult(action="decline")
        try:
            result = await handler(
                {
                    "title": "MCP server requests input",
                    "description": str(params.message),
                    "fields": _schema_fields(dict(params.requested_schema)),
                    "source": "mcp_server",
                },
                dict(params.requested_schema),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MCP Elicitation handler failed")
            return types.ElicitResult(action="decline")
        if not isinstance(result, dict):
            return types.ElicitResult(action="decline")
        action = str(result.get("action") or "decline")
        if action not in {"accept", "decline", "cancel"}:
            action = "decline"
        content = result.get("content") if action == "accept" else None
        return types.ElicitResult(action=action, content=content)

    async def _start_owned(self) -> None:
        raise NotImplementedError

    async def _close_owned(self) -> None:
        raise NotImplementedError

    async def _owner_loop(
        self,
        ready: asyncio.Future[None],
        closed: asyncio.Future[None],
    ) -> None:
        try:
            await self._start_owned()
            if not ready.done():
                ready.set_result(None)
            await self._owner_stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                logger.exception("MCP client owner failed")
        finally:
            try:
                await self._close_owned()
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError):
                    logger.exception("MCP client close failed")
            if not closed.done():
                closed.set_result(None)

    async def _start_owner(self) -> None:
        if self._owner_task is not None:
            raise RuntimeError("MCP client is already started")
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        self._owner_closed = loop.create_future()
        self._owner_stop = asyncio.Event()
        self._owner_task = asyncio.create_task(
            self._owner_loop(ready, self._owner_closed),
            name="mcp-client-owner",
        )
        try:
            await ready
        except BaseException:
            await self._owner_closed
            self._owner_task = None
            raise

    async def _stop_owner(self) -> None:
        task, self._owner_task = self._owner_task, None
        if task is None:
            return
        self._owner_stop.set()
        if self._owner_closed is not None:
            await self._owner_closed
        await asyncio.gather(task, return_exceptions=True)

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

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        elicitation_handler: MCPElicitationHandler | None = None,
    ) -> Any:
        async with self._tool_call_lock:
            self._elicitation_handler = elicitation_handler
            try:
                from mcp import types
                from mcp.client.client import run_input_required_driver
                from mcp.client.session import ClientRequestContext

                session = self._require_session()

                async def retry(input_responses, request_state):
                    return await asyncio.wait_for(
                        session.call_tool(
                            name,
                            arguments,
                            input_responses=input_responses,
                            request_state=request_state,
                            allow_input_required=True,
                        ),
                        timeout=self._timeout,
                    )

                first = await retry(None, None)
                if not isinstance(first, types.InputRequiredResult):
                    return first

                async def dispatch(key, request):
                    return await session.dispatch_input_request(
                        ClientRequestContext(
                            session=session,
                            request_id=key,
                            meta=request.params.meta if request.params else None,
                        ),
                        request,
                    )

                return await run_input_required_driver(
                    first,
                    dispatch=dispatch,
                    retry=retry,
                    max_rounds=8,
                )
            finally:
                self._elicitation_handler = None

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
        self._elicitation_handler: MCPElicitationHandler | None = None
        self._tool_call_lock = asyncio.Lock()
        self._resource_capabilities = MCPResourceCapabilities()
        self._modern = False
        self._resource_subscriptions: set[str] = set()
        self._listen_task: asyncio.Task[None] | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._owner_stop = asyncio.Event()
        self._owner_closed: asyncio.Future[None] | None = None

    def set_notification_handler(
        self,
        handler: MCPNotificationHandler | None,
    ) -> None:
        if self._owner_task is not None:
            raise RuntimeError("MCP notification handler must be set before start")
        self._notification_handler = handler

    async def start(self) -> None:
        await self._start_owner()

    async def _start_owned(self) -> None:
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
                    elicitation_callback=self._elicit,
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
        await self._stop_owner()

    async def _close_owned(self) -> None:
        await self._stop_listener()
        stack, self._stack = self._stack, None
        self._session = None
        self._resource_capabilities = MCPResourceCapabilities()
        if stack is not None:
            await stack.aclose()


class StdioMCPClient(_SessionClientMixin):
    """Own one locally supervised MCP child process over stdin/stdout."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        private_environment: dict[str, str] | None = None,
    ) -> None:
        self._config = config
        self._private_environment = dict(private_environment or {})
        self._timeout = config.timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._notification_handler: MCPNotificationHandler | None = None
        self._elicitation_handler: MCPElicitationHandler | None = None
        self._tool_call_lock = asyncio.Lock()
        self._resource_capabilities = MCPResourceCapabilities()
        self._modern = False
        self._resource_subscriptions: set[str] = set()
        self._listen_task: asyncio.Task[None] | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._owner_stop = asyncio.Event()
        self._owner_closed: asyncio.Future[None] | None = None

    def set_notification_handler(
        self,
        handler: MCPNotificationHandler | None,
    ) -> None:
        if self._owner_task is not None:
            raise RuntimeError("MCP notification handler must be set before start")
        self._notification_handler = handler

    def _environment(self) -> dict[str, str]:
        environment = {
            name: value
            for name in _STDIO_BASE_ENV
            if (value := os.environ.get(name)) is not None
        }
        for name in self._config.inherit_env:
            value = self._private_environment.get(name)
            if value is None:
                value = os.environ.get(name)
            if value is None:
                raise ValueError(
                    f"Required MCP environment variable is not set: {name}"
                )
            environment[name] = value
        for name in self._config.optional_env:
            value = self._private_environment.get(name)
            if value is None:
                value = os.environ.get(name)
            if value is not None:
                environment[name] = value
        return environment

    async def start(self) -> None:
        await self._start_owner()

    async def _start_owned(self) -> None:
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
                    elicitation_callback=self._elicit,
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
        await self._stop_owner()

    async def _close_owned(self) -> None:
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

    structured = _attribute(result, "structured_content", "structuredContent")
    output: dict[str, Any]
    if structured is not None:
        # MCP servers commonly return the same JSON twice: once as structured
        # content and once as a compatibility TextContent block.  Keep the
        # authoritative structured form and only retain non-text content.
        non_text = [block for block in content if block.get("type") != "text"]
        output = {"structured_content": structured}
        if non_text:
            output["content"] = non_text
    else:
        output = {"content": content}
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
        return await self.execute_scoped(None, **kwargs)

    async def execute_scoped(self, scope: Any, **kwargs: Any) -> Any:
        del scope
        from knoa_agent_contracts import InteractionRequested
        from knoa_platform.agent_runtime.tool_step import current_tool_step_context

        context = current_tool_step_context()

        async def elicit(
            display: dict[str, Any],
            resolution_schema: dict[str, Any],
        ) -> Any:
            if context is None or context.interaction is None:
                return {"action": "decline"}
            interaction_id = f"mcp-{context.run_id}-{secrets.token_hex(8)}"
            handle = await context.interaction.begin(
                context.scope,
                context.run_id,
                InteractionRequested(
                    runtime_session_ref=context.scope.session_handle,
                    runtime_turn_ref=context.run_id,
                    occurred_at=time.time(),
                    interaction_id=interaction_id[:128],
                    interaction_epoch=1,
                    kind="mcp_elicitation",
                    display=display,
                    resolution_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["accept", "decline", "cancel"],
                            },
                            "content": resolution_schema,
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                ),
            )
            return await handle.wait()

        try:
            result = await self._client.call_tool(
                self._remote_name,
                kwargs,
                elicitation_handler=elicit,
            )
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
        private_environment_loader: Callable[[], dict[str, str]] | None = None,
        expected_inventory_digest: str = "",
    ) -> None:
        if (config is None) == (config_loader is None):
            raise ValueError("MCP provider requires exactly one configuration source")
        self._server_id = server_id
        self._config = config
        self._active_config: MCPServerConfig | None = config
        self._config_loader = config_loader
        self._client_factory = client_factory
        self._private_environment_loader = private_environment_loader
        self._expected_inventory_digest = expected_inventory_digest.strip()
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
        return self._active_config

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
        self._active_config = config
        if not config.enabled:
            raise ValueError("MCP server is disabled")
        if self._client_factory is not None:
            client = self._client_factory(config)
        else:
            private_environment = (
                self._private_environment_loader()
                if self._private_environment_loader is not None
                else None
            )
            client = (
                create_mcp_client(
                    config,
                    private_environment=private_environment,
                )
                if private_environment is not None
                else create_mcp_client(config)
            )
        self._client = client
        client.set_notification_handler(self._dispatch_notification)
        await client.start()
        discovered_tools = await client.list_tools()
        resources = (
            await client.list_resources()
            if client.resource_capabilities().available
            else ()
        )
        try:
            prompts = await client.list_prompts()
        except Exception:  # noqa: BLE001 - optional MCP capability
            prompts = ()
        if self._expected_inventory_digest and mcp_inventory_digest(
            discovered_tools,
            resources,
            prompts,
        ) != self._expected_inventory_digest:
            raise RuntimeError("MCP inventory drifted after permission review")
        definitions = {definition.name: definition for definition in discovered_tools}
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
    *,
    secret_root: str | Path | None = None,
    inventory_digests: dict[str, str] | None = None,
) -> tuple[MCPServerProvider, ...]:
    from knoa_platform.extensions.mcp_secrets import mcp_private_environment_loader

    return tuple(
        MCPServerProvider(
            server_id,
            config,
            private_environment_loader=mcp_private_environment_loader(
                secret_root,
                server_id,
            ),
            expected_inventory_digest=(inventory_digests or {}).get(server_id, ""),
        )
        for server_id, config in configs.items()
        if config.enabled
    )


def create_mcp_client(
    config: MCPServerConfig,
    *,
    private_environment: dict[str, str] | None = None,
) -> MCPClientPort:
    if config.transport == "stdio":
        return StdioMCPClient(
            config,
            private_environment=private_environment,
        )
    return StreamableHTTPMCPClient(
        config.url,
        timeout_seconds=config.timeout_seconds,
    )
