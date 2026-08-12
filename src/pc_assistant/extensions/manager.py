"""Failure-isolated lifecycle for dynamic capability providers."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from pc_assistant.tools.base import ToolBase, ToolOrigin, ToolOriginKind
from pc_assistant.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ExtensionState(str, Enum):
    CONFIGURED = "configured"
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"


class ExtensionKind(str, Enum):
    MCP = "mcp"
    SKILL = "skill"


@dataclass(frozen=True)
class ExtensionDescriptor:
    extension_id: str
    kind: ExtensionKind

    def __post_init__(self) -> None:
        normalized = self.extension_id.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Extension ID must contain 1-128 characters")
        object.__setattr__(self, "extension_id", normalized)

    @property
    def tool_origin(self) -> ToolOrigin:
        if self.kind is not ExtensionKind.MCP:
            raise ValueError("Skill extensions cannot register executable tools")
        return ToolOrigin(kind=ToolOriginKind.MCP, extension_id=self.extension_id)


@dataclass(frozen=True)
class ExtensionStatus:
    descriptor: ExtensionDescriptor
    state: ExtensionState
    tools: tuple[str, ...] = ()
    detail: str = ""


class ExtensionProvider(Protocol):
    @property
    def descriptor(self) -> ExtensionDescriptor: ...

    async def start(self) -> tuple[ToolBase, ...]: ...

    async def stop(self) -> None: ...


class ExtensionManager:
    """Own provider startup, transactional registration, and shutdown."""

    def __init__(
        self,
        registry: ToolRegistry,
        providers: tuple[ExtensionProvider, ...] = (),
    ) -> None:
        descriptors = [provider.descriptor for provider in providers]
        extension_ids = [descriptor.extension_id for descriptor in descriptors]
        if len(set(extension_ids)) != len(extension_ids):
            raise ValueError("Extension IDs must be unique")
        self._registry = registry
        self._providers = list(providers)
        self._started: list[ExtensionProvider] = []
        self._registered: dict[ExtensionDescriptor, tuple[str, ...]] = {}
        self._statuses: dict[ExtensionDescriptor, ExtensionStatus] = {
            descriptor: ExtensionStatus(descriptor, ExtensionState.CONFIGURED)
            for descriptor in descriptors
        }
        self._running = False
        self._lifecycle_lock = asyncio.Lock()

    @property
    def statuses(self) -> tuple[ExtensionStatus, ...]:
        return tuple(
            self._statuses[provider.descriptor]
            for provider in self._providers
        )

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                raise RuntimeError("ExtensionManager is already started")
            self._running = True
            for provider in self._providers:
                await self._start_provider(provider)

    async def add_provider(self, provider: ExtensionProvider) -> ExtensionStatus:
        """Add one extension and start it immediately when Core is running."""

        async with self._lifecycle_lock:
            descriptor = provider.descriptor
            if descriptor in self._statuses:
                raise ValueError(f"Extension is already configured: {descriptor.extension_id}")
            self._providers.append(provider)
            self._statuses[descriptor] = ExtensionStatus(
                descriptor,
                ExtensionState.CONFIGURED,
            )
            if self._running:
                await self._start_provider(provider)
            return self._statuses[descriptor]

    async def remove_provider(self, provider: ExtensionProvider) -> None:
        """Stop and remove one dynamically added extension."""

        async with self._lifecycle_lock:
            descriptor = provider.descriptor
            if provider not in self._providers:
                return
            origin = descriptor.tool_origin if self._registered.get(descriptor) else None
            for name in reversed(self._registered.pop(descriptor, ())):
                assert origin is not None
                self._registry.unregister(name, origin=origin)
            if provider in self._started:
                self._started.remove(provider)
                try:
                    await provider.stop()
                except Exception:
                    logger.exception("Extension stop failed: %s", descriptor.extension_id)
            self._providers.remove(provider)
            self._statuses.pop(descriptor, None)

    async def stop_provider(self, provider: ExtensionProvider) -> None:
        """Disable one dynamic provider while retaining its configured identity."""

        async with self._lifecycle_lock:
            descriptor = provider.descriptor
            if provider not in self._providers:
                return
            origin = descriptor.tool_origin if self._registered.get(descriptor) else None
            for name in reversed(self._registered.pop(descriptor, ())):
                assert origin is not None
                self._registry.unregister(name, origin=origin)
            if provider in self._started:
                self._started.remove(provider)
                try:
                    await provider.stop()
                except Exception:
                    logger.exception("Extension stop failed: %s", descriptor.extension_id)
            self._statuses[descriptor] = ExtensionStatus(
                descriptor,
                ExtensionState.STOPPED,
            )

    async def _start_provider(self, provider: ExtensionProvider) -> None:
        descriptor = provider.descriptor
        registered: list[str] = []
        origin: ToolOrigin | None = None
        try:
            tools = await provider.start()
            names = [tool.name for tool in tools]
            if len(set(names)) != len(names):
                raise ValueError("Extension returned duplicate tool names")
            if tools:
                origin = descriptor.tool_origin
            for tool in tools:
                assert origin is not None
                self._registry.register(tool, origin=origin)
                registered.append(tool.name)
        except Exception as exc:  # noqa: BLE001 - extension failure isolation boundary
            for name in reversed(registered):
                assert origin is not None
                self._registry.unregister(name, origin=origin)
            try:
                await provider.stop()
            except Exception:
                logger.exception("Extension cleanup failed: %s", descriptor.extension_id)
            detail = f"{type(exc).__name__}: {exc}"[:1000]
            self._statuses[descriptor] = ExtensionStatus(
                descriptor,
                ExtensionState.FAILED,
                detail=detail,
            )
            logger.warning(
                "Extension failed and was isolated: %s (%s)",
                descriptor.extension_id,
                detail,
            )
            return
        names = tuple(registered)
        self._registered[descriptor] = names
        self._started.append(provider)
        self._statuses[descriptor] = ExtensionStatus(
            descriptor,
            ExtensionState.RUNNING,
            tools=names,
        )
        logger.info(
            "Extension started: %s (tools=%d)",
            descriptor.extension_id,
            len(names),
        )

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            providers, self._started = list(reversed(self._started)), []
            for provider in providers:
                descriptor = provider.descriptor
                origin = (
                    descriptor.tool_origin
                    if self._registered.get(descriptor)
                    else None
                )
                for name in reversed(self._registered.pop(descriptor, ())):
                    assert origin is not None
                    self._registry.unregister(name, origin=origin)
                try:
                    await provider.stop()
                except Exception:
                    logger.exception(
                        "Extension stop failed: %s",
                        descriptor.extension_id,
                    )
                self._statuses[descriptor] = ExtensionStatus(
                    descriptor,
                    ExtensionState.STOPPED,
                )
            self._running = False
