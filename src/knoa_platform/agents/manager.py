"""Trusted Agent generation selection, health, capacity, and bounded drain."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from knoa_agent_contracts import AgentRuntime, RuntimeHealth


class AgentNotFoundError(LookupError):
    pass


class AgentDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentGenerationState:
    agent_id: str
    active_generation: str
    draining_generation: str = ""
    active_leases: int = 0
    draining_leases: int = 0
    enabled: bool = True


@dataclass
class _Generation:
    generation_id: str
    runtime: AgentRuntime
    capacity: asyncio.Semaphore
    accepting: bool = True
    active_leases: int = 0


@dataclass
class _ManagedAgent:
    active: _Generation
    enabled: bool
    draining: _Generation | None = None


class AgentManager:
    """Own one active and at most one bounded draining generation per Agent."""

    def __init__(
        self,
        runtimes: Mapping[str, AgentRuntime],
        *,
        default_agent: str = "knoa",
        enabled: Mapping[str, bool] | None = None,
        max_concurrency: Mapping[str, int] | None = None,
        system_agents: frozenset[str] = frozenset(),
        generation_ids: Mapping[str, str] | None = None,
    ) -> None:
        if not runtimes:
            raise ValueError("At least one trusted Agent Runtime is required")
        enabled = enabled or {}
        max_concurrency = max_concurrency or {}
        generation_ids = generation_ids or {}
        self._agents: dict[str, _ManagedAgent] = {}
        self._system_agents = system_agents
        self._swap_guard = asyncio.Lock()
        self._drain_tasks: set[asyncio.Task[None]] = set()
        for agent_id, runtime in runtimes.items():
            self._agents[agent_id] = _ManagedAgent(
                active=self._generation(
                    agent_id,
                    runtime,
                    int(
                        max_concurrency.get(
                            agent_id,
                            runtime.descriptor.limits.max_concurrent_turns,
                        )
                    ),
                    generation_ids.get(agent_id),
                ),
                enabled=bool(enabled.get(agent_id, agent_id == "knoa")),
            )
        self._default_agent = ""
        self.set_default(default_agent)

    @staticmethod
    def _generation(
        agent_id: str,
        runtime: AgentRuntime,
        capacity: int,
        generation_id: str | None,
    ) -> _Generation:
        if runtime.descriptor.agent_id != agent_id:
            raise ValueError("Agent Runtime descriptor does not match registration ID")
        if capacity <= 0:
            raise ValueError("Agent max concurrency must be positive")
        return _Generation(
            generation_id=generation_id or uuid.uuid4().hex,
            runtime=runtime,
            capacity=asyncio.Semaphore(capacity),
        )

    @property
    def default_agent(self) -> str:
        return self._default_agent

    def resolve_agent_id(self, requested: str | None = None) -> str:
        agent_id = (requested or self._default_agent).strip()
        if agent_id in self._system_agents:
            raise AgentNotFoundError(f"Agent '{agent_id}' is not user-selectable")
        return self._resolve_enabled(agent_id)

    def resolve_system_agent_id(self, requested: str) -> str:
        agent_id = requested.strip()
        if agent_id not in self._system_agents:
            raise AgentNotFoundError(f"Agent '{agent_id}' is not a system Agent")
        return self._resolve_enabled(agent_id)

    def _resolve_enabled(self, agent_id: str) -> str:
        managed = self._agents.get(agent_id)
        if managed is None:
            raise AgentNotFoundError(f"Unknown Agent '{agent_id}'")
        if not managed.enabled:
            raise AgentDisabledError(f"Agent '{agent_id}' is disabled")
        return agent_id

    def runtime(self, agent_id: str) -> AgentRuntime:
        resolved = self.resolve_agent_id(agent_id)
        return self._agents[resolved].active.runtime

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

    def generation_state(self) -> tuple[AgentGenerationState, ...]:
        return tuple(
            AgentGenerationState(
                agent_id=agent_id,
                active_generation=managed.active.generation_id,
                draining_generation=(
                    managed.draining.generation_id if managed.draining else ""
                ),
                active_leases=managed.active.active_leases,
                draining_leases=(
                    managed.draining.active_leases if managed.draining else 0
                ),
                enabled=managed.enabled,
            )
            for agent_id, managed in sorted(self._agents.items())
        )

    @asynccontextmanager
    async def lease(self, agent_id: str) -> AsyncIterator[AgentRuntime]:
        resolved = self.resolve_agent_id(agent_id)
        async with self._lease_resolved(resolved) as runtime:
            yield runtime

    @asynccontextmanager
    async def lease_system(self, agent_id: str) -> AsyncIterator[AgentRuntime]:
        resolved = self.resolve_system_agent_id(agent_id)
        async with self._lease_resolved(resolved) as runtime:
            yield runtime

    @asynccontextmanager
    async def _lease_resolved(self, resolved: str) -> AsyncIterator[AgentRuntime]:
        while True:
            managed = self._agents.get(resolved)
            if managed is None:
                raise AgentNotFoundError(f"Unknown Agent '{resolved}'")
            if not managed.enabled:
                raise AgentDisabledError(f"Agent '{resolved}' is disabled")
            generation = managed.active
            await generation.capacity.acquire()
            if (
                self._agents.get(resolved) is managed
                and managed.active is generation
                and generation.accepting
                and managed.enabled
            ):
                generation.active_leases += 1
                break
            generation.capacity.release()
        try:
            yield generation.runtime
        finally:
            generation.active_leases -= 1
            generation.capacity.release()

    async def health(self, agent_id: str) -> RuntimeHealth:
        return await self.runtime(agent_id).health_check()

    async def replace_generations(
        self,
        runtimes: Mapping[str, AgentRuntime],
        *,
        default_agent: str,
        enabled: Mapping[str, bool],
        max_concurrency: Mapping[str, int],
        system_agents: frozenset[str],
        generation_ids: Mapping[str, str],
        drain_seconds: float,
    ) -> None:
        """Health-check a complete generation set, then atomically switch it."""

        if not runtimes:
            raise ValueError("At least one enabled Agent Runtime is required")
        candidates = {
            agent_id: self._generation(
                agent_id,
                runtime,
                int(max_concurrency[agent_id]),
                generation_ids.get(agent_id),
            )
            for agent_id, runtime in runtimes.items()
        }
        changed = {
            agent_id: candidate
            for agent_id, candidate in candidates.items()
            if agent_id not in self._agents
            or self._agents[agent_id].active.generation_id
            != candidate.generation_id
        }
        health = await asyncio.gather(
            *(candidate.runtime.health_check() for candidate in changed.values())
        )
        if any(not item.healthy for item in health):
            raise RuntimeError("agent_generation_preflight_failed")
        if default_agent not in candidates or not enabled.get(default_agent, False):
            raise ValueError("Default Agent must be present and enabled")

        async with self._swap_guard:
            for agent_id, managed in self._agents.items():
                if (
                    agent_id in changed
                    and managed.draining is not None
                    and managed.draining.active_leases
                ):
                    raise RuntimeError(
                        f"Agent '{agent_id}' still has a draining generation"
                    )
            previous = self._agents
            replacement: dict[str, _ManagedAgent] = {}
            for agent_id, candidate in candidates.items():
                old = previous.get(agent_id)
                if (
                    old is not None
                    and old.active.generation_id == candidate.generation_id
                ):
                    replacement[agent_id] = _ManagedAgent(
                        active=old.active,
                        enabled=bool(enabled.get(agent_id, False)),
                        draining=old.draining,
                    )
                    continue
                draining = None
                if old is not None:
                    old.active.accepting = False
                    draining = old.active
                replacement[agent_id] = _ManagedAgent(
                    active=candidate,
                    enabled=bool(enabled.get(agent_id, False)),
                    draining=draining,
                )
            for agent_id, old in previous.items():
                if agent_id not in replacement:
                    old.active.accepting = False
            self._agents = replacement
            self._system_agents = system_agents
            self._default_agent = default_agent
            deadline = time.time() + max(1.0, drain_seconds)
            draining_generations = [
                managed.active
                for agent_id, managed in previous.items()
                if agent_id not in replacement
                or replacement[agent_id].active is not managed.active
            ]
            for generation in draining_generations:
                task = asyncio.create_task(
                    self._drain_generation(generation, deadline)
                )
                self._drain_tasks.add(task)
                task.add_done_callback(self._drain_tasks.discard)

    async def _drain_generation(
        self,
        generation: _Generation,
        deadline: float,
    ) -> None:
        await generation.runtime.drain(deadline)
        while generation.active_leases and time.time() < deadline:
            await asyncio.sleep(0.05)
        for managed in self._agents.values():
            if managed.draining is generation:
                managed.draining = None

    async def drain(self, agent_id: str, deadline: float) -> None:
        managed = self._agents.get(agent_id)
        if managed is None:
            raise AgentNotFoundError(f"Unknown Agent '{agent_id}'")
        managed.enabled = False
        managed.active.accepting = False
        await managed.active.runtime.drain(deadline)
