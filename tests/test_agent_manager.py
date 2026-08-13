from __future__ import annotations

import asyncio

import pytest

from knoa_agent_contracts import AgentDescriptor, RuntimeHealth, RuntimeLimits
from knoa_platform.agents import AgentDisabledError, AgentManager, AgentNotFoundError


class FakeRuntime:
    def __init__(self, agent_id: str, *, max_concurrency: int = 1) -> None:
        self._descriptor = AgentDescriptor(
            agent_id=agent_id,
            display_name=agent_id,
            implementation_version="1",
            limits=RuntimeLimits(max_concurrent_turns=max_concurrency),
        )
        self.drained = False

    @property
    def descriptor(self):
        return self._descriptor

    async def health_check(self):
        return RuntimeHealth(healthy=True, state="ready")

    async def drain(self, deadline: float):
        del deadline
        self.drained = True


def test_manager_resolves_stable_agent_identity_and_dynamic_default() -> None:
    knoa = FakeRuntime("knoa")
    codex = FakeRuntime("codex")
    manager = AgentManager(
        {"knoa": knoa, "codex": codex},
        enabled={"codex": True},
    )

    assert manager.resolve_agent_id() == "knoa"
    manager.set_default("codex")
    assert manager.resolve_agent_id() == "codex"
    assert manager.runtime("knoa") is knoa
    with pytest.raises(AgentDisabledError):
        manager.set_enabled("codex", False)


def test_manager_uses_configured_codex_default_when_agent_is_omitted() -> None:
    manager = AgentManager(
        {"knoa": FakeRuntime("knoa"), "codex": FakeRuntime("codex")},
        default_agent="codex",
        enabled={"knoa": True, "codex": True},
    )

    assert manager.default_agent == "codex"
    assert manager.resolve_agent_id(None) == "codex"


@pytest.mark.asyncio
async def test_manager_enforces_runtime_capacity_and_drain() -> None:
    runtime = FakeRuntime("knoa", max_concurrency=1)
    manager = AgentManager({"knoa": runtime})
    first_acquired = asyncio.Event()
    release = asyncio.Event()
    second_acquired = asyncio.Event()

    async def first() -> None:
        async with manager.lease("knoa"):
            first_acquired.set()
            await release.wait()

    async def second() -> None:
        async with manager.lease("knoa"):
            second_acquired.set()

    first_task = asyncio.create_task(first())
    await first_acquired.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert not second_acquired.is_set()
    release.set()
    await asyncio.gather(first_task, second_task)
    assert second_acquired.is_set()

    await manager.drain("knoa", 10.0)
    assert runtime.drained
    with pytest.raises(AgentDisabledError):
        manager.resolve_agent_id("knoa")


@pytest.mark.asyncio
async def test_system_agent_uses_same_runtime_manager_but_is_not_selectable() -> None:
    reviewer = FakeRuntime("reviewer_agent")
    manager = AgentManager(
        {"knoa": FakeRuntime("knoa"), "reviewer_agent": reviewer},
        enabled={"knoa": True, "reviewer_agent": True},
        system_agents=frozenset({"reviewer_agent"}),
    )

    with pytest.raises(AgentNotFoundError, match="not user-selectable"):
        manager.resolve_agent_id("reviewer_agent")
    async with manager.lease_system("reviewer_agent") as runtime:
        assert runtime is reviewer
