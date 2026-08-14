"""Route explicitly trusted MCP Resources into principal-owned durable Tasks."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from knoa_platform.agent_runtime.contracts import MCPResourceCatalogRecord
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.extensions.mcp import (
    MCPResourceDefinition,
    MCPResourceNotification,
    MCPResourceSnapshot,
    MCPServerProvider,
)
from knoa_platform.extensions.models import MCPResourceTaskConfig
from knoa_platform.tasks import (
    TaskDefinitionState,
    TaskLaunchKind,
)

logger = logging.getLogger(__name__)

_INVALID_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MAX_RESOURCE_EVENT_TEXT_CHARS = 24_000


class MCPTaskCreationPort(Protocol):
    async def list_definitions(
        self,
        principal_id: str,
        *,
        state: TaskDefinitionState | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[Any, ...]: ...

    async def list_event_definitions(
        self,
        event_source: str,
        *,
        limit: int = 1000,
    ) -> tuple[Any, ...]: ...

    async def launch_binding(
        self,
        principal_id: str,
        task_id: str,
    ) -> tuple[str, str] | None: ...

    async def bind_launch(
        self,
        principal_id: str,
        task_id: str,
        *,
        provider_kind: str,
        provider_id: str,
    ) -> None: ...

    async def unbind_launch(self, principal_id: str, task_id: str) -> None: ...


class MCPTriggerIngressPort(Protocol):
    async def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        name: str,
        goal: str,
        tools_enabled: bool = True,
        priority: int = 0,
    ) -> Any: ...

    async def delete(self, principal_id: str, trigger_id: str) -> None: ...

    async def get(self, principal_id: str, trigger_id: str) -> Any: ...

    async def receive(
        self,
        principal_id: str,
        trigger_id: str,
        *,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> Any: ...


class MCPSessionResolutionPort(Protocol):
    def resolve(self, principal_id: str, session_handle: str) -> RuntimeScope: ...

    def isolated_task_scope(
        self,
        source: RuntimeScope,
        task_key: str,
    ) -> RuntimeScope: ...


@dataclass(frozen=True)
class _CanonicalURI:
    text: str
    scheme: str
    authority: str
    segments: tuple[str, ...]


@dataclass
class _RouteState:
    config: MCPResourceTaskConfig
    root: _CanonicalURI
    known: set[str] = field(default_factory=set)


def _canonical_uri(value: str) -> _CanonicalURI:
    raw = value.strip()
    if not raw or len(raw) > 4096 or "\x00" in raw or _INVALID_PERCENT.search(raw):
        raise ValueError("MCP Resource URI is invalid")
    parsed = urlsplit(raw)
    if not parsed.scheme or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("MCP Resource URI is not an allowed absolute URI")
    if parsed.query:
        raise ValueError("MCP Resource Task URI queries are not supported")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("MCP Resource URI has an invalid port") from exc
    hostname = parsed.hostname or ""
    if not hostname and parsed.netloc:
        raise ValueError("MCP Resource URI authority is invalid")
    normalized_host = hostname.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    authority = normalized_host
    if port is not None:
        authority = f"{authority}:{port}"
    decoded_segments: list[str] = []
    for encoded in parsed.path.split("/"):
        if not encoded:
            continue
        decoded = unquote(encoded)
        if (
            decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F for character in decoded
            )
        ):
            raise ValueError("MCP Resource URI contains an unsafe path segment")
        decoded_segments.append(decoded)
    normalized_path = "/" + "/".join(
        quote(segment, safe="-._~!$&'()*+,;=:@") for segment in decoded_segments
    )
    if not decoded_segments:
        normalized_path = ""
    scheme = parsed.scheme.lower()
    return _CanonicalURI(
        text=urlunsplit((scheme, authority, normalized_path, "", "")),
        scheme=scheme,
        authority=authority,
        segments=tuple(decoded_segments),
    )


def _within_scope(root: _CanonicalURI, candidate: _CanonicalURI) -> bool:
    if root.scheme != candidate.scheme or root.authority != candidate.authority:
        return False
    return candidate.segments[: len(root.segments)] == root.segments


def _resource_event_id(
    server_id: str,
    canonical_uri: str,
    snapshot: MCPResourceSnapshot,
) -> str:
    content = tuple(
        (item.uri, item.mime_type, item.text, item.encoded_size)
        for item in snapshot.contents
    )
    digest = hashlib.sha256(
        repr((server_id, canonical_uri, content)).encode()
    ).hexdigest()[:48]
    return f"mcp-resource:{digest}"


def _resource_payload(
    server_id: str,
    uri: str,
    resource: MCPResourceDefinition,
    snapshot: MCPResourceSnapshot,
) -> dict[str, Any]:
    # Trigger payloads are capped by encoded bytes. Keep text comfortably below
    # that boundary even for four-byte Unicode plus JSON metadata.
    remaining = _MAX_RESOURCE_EVENT_TEXT_CHARS
    contents: list[dict[str, Any]] = []
    for item in snapshot.contents:
        text = item.text
        if text:
            text = text[:remaining]
            remaining -= len(text)
        contents.append(
            {
                "uri": item.uri,
                "mime_type": item.mime_type,
                "text": text,
                "encoded_size": item.encoded_size,
            }
        )
        if remaining <= 0:
            break
    return {
        "server_id": server_id,
        "resource_uri": uri,
        "resource_name": resource.name,
        "resource_description": resource.description,
        "contents": contents,
    }


class MCPResourceTaskBridge:
    """Consume standard MCP Resource inventories and create idempotent Tasks."""

    def __init__(
        self,
        providers: tuple[MCPServerProvider, ...],
        tasks: MCPTaskCreationPort,
        sessions: MCPSessionResolutionPort,
        triggers: MCPTriggerIngressPort,
        *,
        reconciliation_interval: float = 60.0,
    ) -> None:
        if not 1.0 <= reconciliation_interval <= 3600.0:
            raise ValueError("MCP Resource reconciliation must be 1-3600 seconds")
        self._providers: dict[str, MCPServerProvider] = {}
        self._routes: dict[str, dict[str, _RouteState]] = {}
        self._subscribed: dict[str, set[str]] = {}
        self._pending_updates: dict[str, set[str]] = {}
        self._catalog: dict[str, tuple[MCPResourceCatalogRecord, ...]] = {}
        self._candidates = tuple(providers)
        self._tasks = tasks
        self._sessions = sessions
        self._triggers = triggers
        self._interval = reconciliation_interval
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    def add_route(
        self,
        provider: MCPServerProvider,
        route_id: str,
        config: MCPResourceTaskConfig,
        *,
        wake: bool = True,
    ) -> None:
        root = self.validate_route(config)
        server_id = provider.server_id
        routes = self._routes.setdefault(server_id, {})
        if route_id in routes:
            raise ValueError("MCP Resource Task route is already active")
        if server_id not in self._providers:
            self._providers[server_id] = provider
            self._subscribed[server_id] = set()
            self._pending_updates[server_id] = set()
            provider.add_notification_listener(self._listener(server_id))
        elif self._providers[server_id] is not provider:
            raise ValueError("MCP Resource Task server ID is already active")
        routes[route_id] = _RouteState(config, root)
        if wake:
            self._wake.set()

    def add_provider(
        self,
        provider: MCPServerProvider,
        *,
        wake: bool = True,
    ) -> None:
        """Attach one running MCP provider for discovery and Task event ingress."""

        if self._providers.get(provider.server_id) is provider:
            return
        config = provider.config
        if config is None:
            raise ValueError("MCP provider has no active configuration")
        self._providers[provider.server_id] = provider
        self._subscribed[provider.server_id] = set()
        self._pending_updates[provider.server_id] = set()
        self._routes.setdefault(provider.server_id, {})
        provider.add_notification_listener(self._listener(provider.server_id))
        # Resource-to-task routing is defined by active Task Definitions.
        # ``resource_tasks`` remains readable for one-version compatibility,
        # but is intentionally not consulted by the runtime bridge.
        if wake:
            self._wake.set()

    async def remove_provider(self, provider: MCPServerProvider) -> None:
        """Detach one provider before it is stopped or replaced."""

        server_id = provider.server_id
        if self._providers.get(server_id) is not provider:
            return
        for uri in tuple(self._subscribed.get(server_id, ())):
            try:
                await provider.unsubscribe_resource(uri)
            except Exception:  # noqa: BLE001 - provider replacement is fail-isolated
                logger.debug(
                    "MCP Resource unsubscribe failed during provider replacement: %s %s",
                    server_id,
                    uri,
                )
        self._providers.pop(server_id, None)
        self._routes.pop(server_id, None)
        self._subscribed.pop(server_id, None)
        self._pending_updates.pop(server_id, None)
        self._catalog.pop(server_id, None)

    def catalog(self) -> tuple[MCPResourceCatalogRecord, ...]:
        return tuple(
            resource
            for server_id in sorted(self._catalog)
            for resource in self._catalog[server_id]
        )

    @staticmethod
    def validate_route(config: MCPResourceTaskConfig) -> _CanonicalURI:
        return _canonical_uri(config.uri)

    def _listener(
        self, server_id: str
    ) -> Callable[[MCPResourceNotification], Awaitable[None]]:
        async def receive(notification: MCPResourceNotification) -> None:
            if notification.kind == "updated" and notification.uri:
                self._pending_updates[server_id].add(notification.uri)
            self._wake.set()

        return receive

    @property
    def started(self) -> bool:
        return self._worker is not None

    async def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("MCPResourceTaskBridge is already started")
        for provider in self._candidates:
            if provider.server_id in self._providers:
                continue
            try:
                self.add_provider(provider)
            except Exception:  # noqa: BLE001 - one provider must not block Core startup
                logger.warning(
                    "MCP Resource Task provider could not be attached: %s",
                    provider.server_id,
                )
        self._worker = asyncio.create_task(
            self._worker_loop(),
            name="mcp-resource-task-bridge",
        )
        self._wake.set()

    async def stop(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        for server_id, provider in tuple(self._providers.items()):
            for uri in tuple(self._subscribed[server_id]):
                try:
                    await provider.unsubscribe_resource(uri)
                except Exception:  # noqa: BLE001 - remote MCP shutdown isolation
                    logger.debug(
                        "MCP Resource unsubscribe failed during shutdown: %s %s",
                        server_id,
                        uri,
                    )
            self._subscribed[server_id].clear()

    async def _worker_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MCP Resource Task reconciliation failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def reconcile_once(self) -> None:
        # Keep explicit one-shot reconciliation useful without making Core
        # composition attach providers before ExtensionManager starts them.
        for provider in self._candidates:
            if provider.server_id not in self._providers:
                self.add_provider(provider, wake=False)
        for server_id, provider in tuple(self._providers.items()):
            try:
                await self._reconcile_provider(server_id, provider)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "MCP Resource provider reconciliation failed: %s", server_id
                )

    async def _reconcile_provider(
        self,
        server_id: str,
        provider: MCPServerProvider,
    ) -> None:
        if self._providers.get(server_id) is not provider:
            return
        capabilities = provider.resource_capabilities()
        if not capabilities.available:
            logger.warning("MCP server does not expose Resources: %s", server_id)
            return
        resources = await provider.list_resources()
        canonical_resources: dict[str, MCPResourceDefinition] = {}
        for resource in resources:
            try:
                canonical = _canonical_uri(resource.uri)
            except ValueError:
                logger.warning("Ignoring unsafe MCP Resource URI from %s", server_id)
                continue
            canonical_resources[canonical.text] = resource

        self._catalog[server_id] = tuple(
            MCPResourceCatalogRecord(
                server_id=server_id,
                uri=canonical_uri,
                name=resource.name,
                description=resource.description,
                mime_type=resource.mime_type,
                subscribable=capabilities.subscribe,
            )
            for canonical_uri, resource in sorted(canonical_resources.items())
        )

        desired_subscriptions: set[str] = set()
        definitions = await self._tasks.list_event_definitions(
            f"mcp:{server_id}",
            limit=5000,
        )
        owned_definitions: list[Any] = []
        for definition in definitions:
            try:
                await asyncio.to_thread(
                    self._sessions.resolve,
                    definition.principal_id,
                    definition.session_handle,
                )
            except Exception:  # noqa: BLE001 - session repository boundary is generic
                logger.warning(
                    "MCP Event Task has no owned Session: %s/%s",
                    server_id,
                    definition.task_id,
                )
                continue
            await self._ensure_trigger_binding(definition)
            owned_definitions.append(definition)
        for canonical_uri in sorted(canonical_resources):
            candidate = _canonical_uri(canonical_uri)
            matching = tuple(
                definition
                for definition in owned_definitions
                if self._matches_definition(
                    definition,
                    server_id=server_id,
                    resource_uri=candidate,
                )
            )
            if not matching:
                continue
            desired_subscriptions.add(canonical_uri)
            await self._emit_resource_event(
                server_id,
                provider,
                canonical_uri,
                canonical_resources[canonical_uri],
                matching,
            )
        if capabilities.subscribe and self._providers.get(server_id) is provider:
            await self._sync_subscriptions(
                provider,
                server_id,
                desired_subscriptions,
            )
        pending = self._pending_updates.get(server_id)
        if pending is not None and self._providers.get(server_id) is provider:
            pending.clear()

    async def _sync_subscriptions(
        self,
        provider: MCPServerProvider,
        server_id: str,
        uris: set[str],
    ) -> None:
        if self._providers.get(server_id) is not provider:
            return
        subscribed = self._subscribed.get(server_id)
        if subscribed is None:
            return
        for uri in sorted(uris - subscribed):
            try:
                await provider.subscribe_resource(uri)
            except Exception:  # noqa: BLE001 - remote MCP degradation is isolated
                logger.warning(
                    "MCP Resource subscription failed: %s %s", server_id, uri
                )
                continue
            subscribed.add(uri)
        for uri in sorted(subscribed - uris):
            try:
                await provider.unsubscribe_resource(uri)
            except Exception:  # noqa: BLE001 - remote MCP degradation is isolated
                logger.warning(
                    "MCP Resource unsubscription failed: %s %s", server_id, uri
                )
                continue
            subscribed.remove(uri)

    @staticmethod
    def _matches_definition(
        definition: Any,
        *,
        server_id: str,
        resource_uri: _CanonicalURI,
    ) -> bool:
        policy = definition.launch_policy
        if policy.kind is not TaskLaunchKind.EVENT:
            return False
        if policy.event_source != f"mcp:{server_id}":
            return False
        config = policy.source_config
        prefix = config.get("resource_uri_prefix")
        if isinstance(prefix, str):
            try:
                root = _canonical_uri(prefix)
                if root.text == resource_uri.text:
                    return bool(config.get("include_root", True))
                return bool(config.get("include_descendants", False)) and _within_scope(
                    root,
                    resource_uri,
                )
            except ValueError:
                return False
        return False

    async def _emit_resource_event(
        self,
        server_id: str,
        provider: MCPServerProvider,
        uri: str,
        resource: MCPResourceDefinition,
        definitions: tuple[Any, ...],
    ) -> None:
        snapshot = await provider.read_resource(uri)
        payload = _resource_payload(server_id, uri, resource, snapshot)
        external_event_id = _resource_event_id(server_id, uri, snapshot)
        for definition in definitions:
            trigger_id = await self._ensure_trigger_binding(definition)
            if trigger_id is None:
                logger.warning(
                    "Event Task has no active Trigger binding: %s",
                    definition.task_id,
                )
                continue
            await self._triggers.receive(
                definition.principal_id,
                trigger_id,
                external_event_id=external_event_id,
                payload=payload,
            )

    async def _ensure_trigger_binding(self, definition: Any) -> str | None:
        binding = await self._tasks.launch_binding(
            definition.principal_id,
            definition.task_id,
        )
        if binding is not None:
            if binding[0] != "event":
                logger.error(
                    "MCP Event Task has a non-event launch binding: task=%s kind=%s",
                    definition.task_id,
                    binding[0],
                )
                return None
            try:
                await self._triggers.get(definition.principal_id, binding[1])
            except LookupError:
                await self._tasks.unbind_launch(
                    definition.principal_id,
                    definition.task_id,
                )
            else:
                return binding[1]

        trigger = await self._triggers.create(
            RuntimeScope(
                principal_id=definition.principal_id,
                session_handle=definition.session_handle,
            ),
            client_request_id=f"mcp-event-binding:{definition.task_id}",
            name=definition.title,
            goal=definition.goal,
            tools_enabled=definition.tools_enabled,
            priority=definition.priority,
        )
        try:
            await self._tasks.bind_launch(
                definition.principal_id,
                definition.task_id,
                provider_kind="event",
                provider_id=trigger.trigger_id,
            )
        except Exception:
            current = await self._tasks.launch_binding(
                definition.principal_id,
                definition.task_id,
            )
            if current is not None and current[0] == "event":
                return current[1]
            try:
                await self._triggers.delete(
                    definition.principal_id,
                    trigger.trigger_id,
                )
            except Exception:
                logger.exception(
                    "Failed to discard repaired MCP Event Trigger: %s",
                    trigger.trigger_id,
                )
            raise
        logger.info(
            "Repaired missing MCP Event Trigger binding: task=%s trigger=%s",
            definition.task_id,
            trigger.trigger_id,
        )
        return trigger.trigger_id
