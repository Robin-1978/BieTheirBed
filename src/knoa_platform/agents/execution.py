"""Platform application service orchestrating trusted Agent Runtime turns."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from knoa_agent_contracts import (
    ArtifactPart,
    ArtifactReference,
    CreateRuntimeSession,
    InteractionRequested,
    McpEndpointGrant,
    ResumeRuntimeSession,
    RuntimeInterruptCommand,
    RuntimeInteractionResolution,
    RuntimeTurnEvent,
    RuntimeTurnRequest,
    TextPart,
    ToolCallStarted,
    TurnFinished,
    UsageReported,
)
from knoa_platform.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from knoa_platform.agent_runtime.tool_step import ConfirmationPort, ToolCommitPort
from knoa_platform.agents.bindings import (
    AgentSessionBinding,
    AgentSessionBindingRepository,
)
from knoa_platform.agents.manager import AgentManager
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.capabilities import CapabilityGateway
from knoa_platform.tools.base import ToolCapability


class InteractionHandle(Protocol):
    async def wait(self) -> Any: ...


class InteractionPort(Protocol):
    async def begin(
        self,
        scope: RuntimeScope,
        run_id: str,
        event: InteractionRequested,
    ) -> InteractionHandle: ...


@dataclass(frozen=True)
class ExecuteAgentTurn:
    scope: RuntimeScope
    turn_id: str
    client_request_id: str
    input: str
    attachments: tuple[ArtifactAttachment, ...]
    tools_enabled: bool
    cancellation: asyncio.Event
    agent_id: str | None = None
    confirmation: ConfirmationPort | None = None
    tool_commit: ToolCommitPort | None = None
    interaction: InteractionPort | None = None


class AgentExecutionService:
    """The only Platform component that resolves and invokes Agent Runtimes."""

    def __init__(
        self,
        manager: AgentManager,
        bindings: AgentSessionBindingRepository,
        gateway: CapabilityGateway,
        artifacts: ArtifactStore,
        *,
        capabilities_for: Callable[[RuntimeScope], frozenset[ToolCapability]],
        external_mcp_endpoint: Callable[[], str] | None = None,
        agent_config_digest: Callable[[str], str] | None = None,
        usage_observer: Callable[
            [ExecuteAgentTurn, UsageReported], Awaitable[None]
        ]
        | None = None,
        turn_observer: Callable[
            [ExecuteAgentTurn, TurnFinished, int, float], Awaitable[None]
        ]
        | None = None,
    ) -> None:
        self._manager = manager
        self._bindings = bindings
        self._gateway = gateway
        self._artifacts = artifacts
        self._capabilities_for = capabilities_for
        self._external_mcp_endpoint = external_mcp_endpoint
        self._agent_config_digest = agent_config_digest or (
            lambda agent_id: hashlib.sha256(agent_id.encode()).hexdigest()
        )
        self._usage_observer = usage_observer
        self._turn_observer = turn_observer
        self._binding_locks: dict[str, asyncio.Lock] = {}
        self._binding_locks_guard = asyncio.Lock()

    async def execute_turn(
        self,
        request: ExecuteAgentTurn,
    ) -> AsyncIterator[RuntimeTurnEvent]:
        started = time.monotonic()
        binding = await self._ensure_binding(request.scope, request.agent_id)
        async with self._manager.lease(binding.agent_id) as runtime:
            session = await runtime.resume_session(
                ResumeRuntimeSession(
                    operation_id=f"resume:{request.turn_id}",
                    session=binding.runtime_session(),
                )
            )
            capabilities = (
                self._capabilities_for(request.scope)
                if request.tools_enabled
                else frozenset()
            )
            grant = await self._gateway.grants.issue(
                scope=request.scope,
                run_id=request.turn_id,
                client_request_id=request.client_request_id,
                capabilities=capabilities,
                cancellation=request.cancellation,
                confirmation=request.confirmation,
                tool_commit=request.tool_commit,
                interaction=request.interaction,
                artifact_ids=frozenset(
                    attachment.artifact_id for attachment in request.attachments
                ),
                binding_epoch=session.binding_epoch,
            )
            external = binding.agent_id != "knoa"
            if external and self._external_mcp_endpoint is None:
                raise RuntimeError("External Agent requires the capability MCP host")
            endpoint = McpEndpointGrant(
                server_id="knoa-platform-capabilities",
                transport="streamable_http" if external else "in_memory",
                endpoint=(
                    self._external_mcp_endpoint()
                    if external and self._external_mcp_endpoint is not None
                    else "memory://knoa-platform-capabilities"
                ),
                authorization=grant.token,
                expires_at=grant.expires_at,
                scope_digest=grant.scope_digest,
                binding_epoch=grant.binding_epoch,
            )
            try:
                turn = await runtime.start_turn(
                    RuntimeTurnRequest(
                        session=session,
                        operation_id=request.turn_id,
                        input=self._input_parts(request),
                        mcp=endpoint,
                    )
                )
                interrupt = asyncio.create_task(
                    self._interrupt_on_cancel(
                        runtime,
                        session,
                        turn.runtime_turn_ref,
                        request,
                    )
                )
                terminal_count = 0
                tool_calls = 0
                terminal: TurnFinished | None = None
                try:
                    async for event in turn.events:
                        if isinstance(event, ToolCallStarted):
                            tool_calls += 1
                        if isinstance(event, UsageReported) and self._usage_observer:
                            await self._usage_observer(request, event)
                        if isinstance(event, TurnFinished):
                            terminal_count += 1
                            terminal = event
                        if terminal_count > 1:
                            raise RuntimeError("Agent Runtime emitted multiple terminals")
                        interaction_handle: InteractionHandle | None = None
                        if isinstance(event, InteractionRequested):
                            if event.kind != "user_input" or request.interaction is None:
                                raise RuntimeError(
                                    "Agent Runtime requested an unsupported interaction"
                                )
                            interaction_handle = await request.interaction.begin(
                                request.scope,
                                request.turn_id,
                                event,
                            )
                        yield event
                        if interaction_handle is not None:
                            value = await interaction_handle.wait()
                            result = await runtime.resolve_interaction(
                                RuntimeInteractionResolution(
                                    session=session,
                                    runtime_turn_ref=turn.runtime_turn_ref,
                                    interaction_id=event.interaction_id,
                                    interaction_epoch=event.interaction_epoch,
                                    command_id=(
                                        f"interaction:{request.turn_id}:"
                                        f"{event.interaction_id}:{event.interaction_epoch}"
                                    ),
                                    value=value,
                                )
                            )
                            if result.status != "accepted":
                                raise RuntimeError(
                                    "Agent Runtime rejected the interaction resolution"
                                )
                finally:
                    interrupt.cancel()
                    await asyncio.gather(interrupt, return_exceptions=True)
                if terminal_count != 1:
                    raise RuntimeError("Agent Runtime ended without one terminal")
                if terminal is not None and self._turn_observer is not None:
                    await self._turn_observer(
                        request,
                        terminal,
                        tool_calls,
                        max(0.0, (time.monotonic() - started) * 1000),
                    )
            finally:
                await self._gateway.grants.revoke(grant.token)

    async def health(self, agent_id: str | None = None):
        return await self._manager.health(
            self._manager.resolve_agent_id(agent_id)
        )

    async def _ensure_binding(
        self,
        scope: RuntimeScope,
        requested_agent_id: str | None = None,
    ) -> AgentSessionBinding:
        existing = await asyncio.to_thread(self._bindings.get, scope)
        if existing is not None:
            if requested_agent_id is not None:
                selected = self._manager.resolve_agent_id(requested_agent_id)
                if existing.agent_id != selected:
                    raise ValueError(
                        "Session is already bound to a different Agent"
                    )
            return existing
        lock = await self._binding_lock(scope.session_handle)
        async with lock:
            existing = await asyncio.to_thread(self._bindings.get, scope)
            if existing is not None:
                return existing
            agent_id = self._manager.resolve_agent_id(requested_agent_id)
            async with self._manager.lease(agent_id) as runtime:
                session = await runtime.create_session(
                    CreateRuntimeSession(
                        operation_id=f"bind:{scope.session_handle}:{agent_id}:1",
                        binding_epoch=1,
                    )
                )
            return await asyncio.to_thread(
                self._bindings.create,
                scope,
                session,
                agent_config_digest=self._agent_config_digest(agent_id),
            )

    async def _binding_lock(self, session_handle: str) -> asyncio.Lock:
        async with self._binding_locks_guard:
            return self._binding_locks.setdefault(session_handle, asyncio.Lock())

    def _input_parts(self, request: ExecuteAgentTurn):
        parts = []
        if request.input:
            parts.append(TextPart(text=request.input))
        for attachment in request.attachments:
            metadata = self._artifacts.metadata(
                request.scope.session_handle,
                attachment.artifact_id,
            )
            digest = str(metadata["content_sha256"])
            if len(digest) != 64:
                raise ValueError("Platform Artifact is missing a stable digest")
            kind = str(metadata["kind"])
            parts.append(
                ArtifactPart(
                    artifact=ArtifactReference(
                        artifact_id=attachment.artifact_id,
                        name=str(metadata["name"]),
                        media_type=str(metadata["media_type"]),
                        size_bytes=int(metadata["size"]),
                        sha256=digest,
                    ),
                    resource_uri=CapabilityGateway.artifact_uri(
                        attachment.artifact_id
                    ),
                    presentation="image" if kind == "image" else "file",
                    caption=attachment.caption,
                )
            )
        if not parts:
            raise ValueError("Agent Turn requires text or an Artifact")
        return tuple(parts)

    @staticmethod
    async def _interrupt_on_cancel(
        runtime,
        session,
        runtime_turn_ref: str,
        request: ExecuteAgentTurn,
    ) -> None:
        await request.cancellation.wait()
        await runtime.interrupt_turn(
            RuntimeInterruptCommand(
                session=session,
                runtime_turn_ref=runtime_turn_ref,
                command_id=f"interrupt:{request.turn_id}:{time.time_ns()}",
                reason="Platform cancellation requested",
            )
        )
