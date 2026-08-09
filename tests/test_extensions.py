from __future__ import annotations

import pytest

from pc_assistant.extensions import (
    ExtensionDescriptor,
    ExtensionManager,
    ExtensionState,
)
from pc_assistant.tools.base import (
    ToolBase,
    ToolEffect,
    ToolOriginKind,
    ToolRisk,
)
from pc_assistant.tools.registry import ToolRegistry


class _Tool(ToolBase):
    effect = ToolEffect.READ_ONLY
    risk = ToolRisk.LOW

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name

    async def execute(self, **kwargs):
        return kwargs

    def schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }


class _Provider:
    def __init__(
        self,
        extension_id: str,
        tools: tuple[ToolBase, ...] = (),
        *,
        failure: Exception | None = None,
    ) -> None:
        self._descriptor = ExtensionDescriptor(
            extension_id=extension_id,
            kind=ToolOriginKind.MCP,
        )
        self._tools = tools
        self._failure = failure
        self.started = False
        self.stopped = False

    @property
    def descriptor(self) -> ExtensionDescriptor:
        return self._descriptor

    async def start(self) -> tuple[ToolBase, ...]:
        self.started = True
        if self._failure is not None:
            raise self._failure
        return self._tools

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_extension_manager_registers_origin_and_cleans_up() -> None:
    registry = ToolRegistry()
    provider = _Provider("mcp:docs", (_Tool("mcp__docs__search"),))
    manager = ExtensionManager(registry, (provider,))

    await manager.start()

    assert registry.list_tools() == ["mcp__docs__search"]
    assert registry.origin("mcp__docs__search") == provider.descriptor.origin
    assert manager.statuses[0].state is ExtensionState.RUNNING

    await manager.stop()

    assert registry.list_tools() == []
    assert provider.stopped is True
    assert manager.statuses[0].state is ExtensionState.STOPPED


@pytest.mark.asyncio
async def test_extension_failure_isolated_from_other_providers() -> None:
    registry = ToolRegistry()
    failed = _Provider("mcp:failed", failure=RuntimeError("offline"))
    healthy = _Provider("mcp:healthy", (_Tool("mcp__healthy__ping"),))
    manager = ExtensionManager(registry, (failed, healthy))

    await manager.start()

    assert failed.stopped is True
    assert registry.list_tools() == ["mcp__healthy__ping"]
    assert [status.state for status in manager.statuses] == [
        ExtensionState.FAILED,
        ExtensionState.RUNNING,
    ]

    await manager.stop()


@pytest.mark.asyncio
async def test_extension_registration_is_transactional_per_provider() -> None:
    registry = ToolRegistry()
    registry.register(_Tool("occupied"))
    provider = _Provider(
        "mcp:conflict",
        (_Tool("temporary"), _Tool("occupied")),
    )
    manager = ExtensionManager(registry, (provider,))

    await manager.start()

    assert registry.list_tools() == ["occupied"]
    assert registry.origin("temporary") is None
    assert manager.statuses[0].state is ExtensionState.FAILED
    assert provider.stopped is True
