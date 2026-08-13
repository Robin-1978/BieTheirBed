"""Trusted Agent instance selection, health, capacity, and drain lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from knoa_agent_contracts import AgentRuntime, RuntimeHealth


class AgentNotFoundError(LookupError):
    pass


class AgentDisabledError(RuntimeError):
    pass


@dataclass
class _ManagedAgent:
    runtime: AgentRuntime
    enabled: bool
    capacity: asyncio.Semaphore
    active_leases: int = 0


class AgentManager:
    """Manage a static trusted implementation set with dynamic enable/default state."""

    def __init__(
        self,
        runtimes: Mapping[str, AgentRuntime],
        *,
        default_agent: str = "knoa",
        enabled: Mapping[str, bool] | None = None,
        max_concurrency: Mapping[str, int] | None = None,
    ) -> None:
        if not runtimes:
            raise ValueError("At least one trusted Agent Runtime is required")
        enabled = enabled or {}
        max_concurrency = max_concurrency or {}
        self._agents: dict[str, _ManagedAgent] = {}
        for agent_id, runtime in runtimes.items():
            if runtime.descriptor.agent_id != agent_id:
                raise ValueError("Agent Runtime descriptor does not match registration ID")
            capacity = int(
                max_concurrency.get(
                    agent_id,
                    runtime.descriptor.limits.max_concurrent_turns,
                )
            )
            if capacity <= 0:
                raise ValueError("Agent max concurrency must be positive")
            self._agents[agent_id] = _ManagedAgent(
                runtime=runtime,
                enabled=bool(enabled.get(agent_id, agent_id == "knoa")),
                capacity=asyncio.Semaphore(capacity),
            )
        self._default_agent = ""
        self.set_default(default_agent)

    @property
    def default_agent(self) -> str:
        return self._default_agent

    def resolve_agent_id(self, requested: str | None = None) -> str:
        agent_id = (requested or self._default_agent).strip()
        managed = self._agents.get(agent_id)
        if managed is None:
            raise AgentNotFoundError(f"Unknown Agent '{agent_id}'")
        if not managed.enabled:
            raise AgentDisabledError(f"Agent '{agent_id}' is disabled")
        return agent_id

    def runtime(self, agent_id: str) -> AgentRuntime:
        resolved = self.resolve_agent_id(agent_id)
        return self._agents[resolved].runtime

    def set_default(self, agent_id: str) -> None:
        resolved = self.resolve_agent_id(agent_id)
        self._default_agent = resolved

    def set_enabled(self, agent_id: str, enabled: bool) -> None:
        managed = self._agents.get(agent_id)
        if managed is None:
            raise AgentNotFoundError(f"Unknown Agent '{agent_id}'")
        if not enabled and agent_id == self._default_agent:
            raise AgentDisabledError("Default Agent cannot be disabled")
        managed.enabled = enabled

    @asynccontextmanager
    async def lease(self, agent_id: str) -> AsyncIterator[AgentRuntime]:
        resolved = self.resolve_agent_id(agent_id)
        managed = self._agents[resolved]
        await managed.capacity.acquire()
        managed.active_leases += 1
        try:
            if not managed.enabled:
                raise AgentDisabledError(f"Agent '{resolved}' is disabled")
            yield managed.runtime
        finally:
            managed.active_leases -= 1
            managed.capacity.release()

    async def health(self, agent_id: str) -> RuntimeHealth:
        return await self.runtime(agent_id).health_check()

    async def drain(self, agent_id: str, deadline: float) -> None:
        managed = self._agents.get(agent_id)
        if managed is None:
            raise AgentNotFoundError(f"Unknown Agent '{agent_id}'")
        managed.enabled = False
        await managed.runtime.drain(deadline)
