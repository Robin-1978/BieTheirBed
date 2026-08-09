"""Failure-isolated lifecycle for dynamic capability providers."""
from __future__ import annotations

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


@dataclass(frozen=True)
class ExtensionDescriptor:
    extension_id: str
    kind: ToolOriginKind

    def __post_init__(self) -> None:
        normalized = self.extension_id.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Extension ID must contain 1-128 characters")
        if self.kind is ToolOriginKind.BUILTIN:
            raise ValueError("Built-in tools are not lifecycle-managed extensions")
        object.__setattr__(self, "extension_id", normalized)

    @property
    def origin(self) -> ToolOrigin:
        return ToolOrigin(kind=self.kind, extension_id=self.extension_id)


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
        self._providers = providers
        self._started: list[ExtensionProvider] = []
        self._registered: dict[ExtensionDescriptor, tuple[str, ...]] = {}
        self._statuses: dict[ExtensionDescriptor, ExtensionStatus] = {
            descriptor: ExtensionStatus(descriptor, ExtensionState.CONFIGURED)
            for descriptor in descriptors
        }
        self._running = False

    @property
    def statuses(self) -> tuple[ExtensionStatus, ...]:
        return tuple(
            self._statuses[provider.descriptor]
            for provider in self._providers
        )

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("ExtensionManager is already started")
        self._running = True
        for provider in self._providers:
            await self._start_provider(provider)

    async def _start_provider(self, provider: ExtensionProvider) -> None:
        descriptor = provider.descriptor
        registered: list[str] = []
        try:
            tools = await provider.start()
            names = [tool.name for tool in tools]
            if len(set(names)) != len(names):
                raise ValueError("Extension returned duplicate tool names")
            for tool in tools:
                self._registry.register(tool, origin=descriptor.origin)
                registered.append(tool.name)
        except Exception as exc:
            for name in reversed(registered):
                self._registry.unregister(name, origin=descriptor.origin)
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
        providers, self._started = list(reversed(self._started)), []
        for provider in providers:
            descriptor = provider.descriptor
            for name in reversed(self._registered.pop(descriptor, ())):
                self._registry.unregister(name, origin=descriptor.origin)
            try:
                await provider.stop()
            except Exception:
                logger.exception("Extension stop failed: %s", descriptor.extension_id)
            self._statuses[descriptor] = ExtensionStatus(
                descriptor,
                ExtensionState.STOPPED,
            )
        self._running = False
