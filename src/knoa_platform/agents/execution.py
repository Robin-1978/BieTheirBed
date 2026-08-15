"""Platform application service orchestrating trusted Agent Runtime turns."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from knoa_agent_contracts import (
    ArtifactPart,
    ArtifactReference,
    CreateRuntimeSession,
    InteractionRequested,
    McpEndpointGrant,
    ResumeRuntimeSession,
    RuntimeInteractionResolution,
    RuntimeInterruptCommand,
    RuntimeTurnContext,
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
from knoa_platform.agents.definitions import (
    AgentDefinitionResolver,
    ResolvedInvocationPolicy,
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
    invocation_kind: str = "user"
    caller_id: str = ""
    parent_policy: ResolvedInvocationPolicy | None = None
    resolved_policy: ResolvedInvocationPolicy | None = None
    confirmation: ConfirmationPort | None = None
    tool_commit: ToolCommitPort | None = None
    interaction: InteractionPort | None = None


@dataclass
class _SessionTurnLease:
    lock: asyncio.Lock
    users: int = 0


class AgentExecutionService:
    """The only Platform component that resolves and invokes Agent Runtimes."""

    def __init__(
        self,
        manager: AgentManager,
        bindings: AgentSessionBindingRepository,
        gateway: CapabilityGateway,
        artifacts: ArtifactStore,
        *,
        resolver_for: Callable[[], AgentDefinitionResolver],
        capabilities_for: Callable[[RuntimeScope], frozenset[ToolCapability]],
        installed_skills: Callable[[], frozenset[str]] = lambda: frozenset(),
        policy_snapshot_for: Callable[
            [str], ResolvedInvocationPolicy | None
        ] = lambda _turn_id: None,
        external_mcp_endpoint: Callable[[], str] | None = None,
        policy_observer: Callable[
            [ExecuteAgentTurn, ResolvedInvocationPolicy], Awaitable[None]
        ]
        | None = None,
        usage_observer: Callable[
            [ExecuteAgentTurn, UsageReported], Awaitable[None]
        ]
        | None = None,
        turn_observer: Callable[
            [ExecuteAgentTurn, TurnFinished, int, float], Awaitable[None]
        ]
        | None = None,
        context_provider: Callable[
            [RuntimeScope, str, frozenset[str], frozenset[str]],
            Awaitable[RuntimeTurnContext],
        ]
        | None = None,
    ) -> None:
        self._manager = manager
        self._bindings = bindings
        self._gateway = gateway
        self._artifacts = artifacts
        self._resolver_for = resolver_for
        self._capabilities_for = capabilities_for
        self._installed_skills = installed_skills
        self._policy_snapshot_for = policy_snapshot_for
        self._external_mcp_endpoint = external_mcp_endpoint
        self._policy_observer = policy_observer
        self._usage_observer = usage_observer
        self._turn_observer = turn_observer
        self._context_provider = context_provider
        self._binding_locks: dict[str, asyncio.Lock] = {}
        self._binding_locks_guard = asyncio.Lock()
        self._turn_leases: dict[tuple[str, str], _SessionTurnLease] = {}
        self._turn_leases_guard = asyncio.Lock()

    async def execute_turn(
        self,
        request: ExecuteAgentTurn,
    ) -> AsyncIterator[RuntimeTurnEvent]:
        async with self._session_turn_lease(request.scope):
            async for event in self._execute_turn_locked(request):
                yield event

    async def _execute_turn_locked(
        self,
        request: ExecuteAgentTurn,
    ) -> AsyncIterator[RuntimeTurnEvent]:
        started = time.monotonic()
        resolver = self._resolver_for()
        policy_snapshot = (
            request.resolved_policy or self._policy_snapshot_for(request.turn_id)
        )
        if request.invocation_kind == "delegate" and policy_snapshot is None:
            raise PermissionError("missing_delegation_policy")
        if policy_snapshot is not None:
            if (
                resolver.definition_digest(policy_snapshot.agent_id)
                != policy_snapshot.agent_definition_digest
            ):
                raise RuntimeError("agent_definition_changed")
        principal_capabilities = self._capabilities_for(request.scope)
        available_tools = self._gateway.available_tool_names(
            principal_capabilities
        )
        policy = resolver.resolve_policy(
            (
                policy_snapshot.agent_id
                if policy_snapshot is not None
                else request.agent_id
            ),
            invocation_kind=(
                policy_snapshot.invocation_kind
                if policy_snapshot is not None
                else request.invocation_kind  # type: ignore[arg-type]
            ),
            caller_id=(
                policy_snapshot.caller_id
                if policy_snapshot is not None
                else request.caller_id or request.scope.principal_id
            ),
            principal_capabilities=frozenset(
                item.value for item in principal_capabilities
            ),
            available_tools=available_tools,
            installed_skills=self._installed_skills(),
            requested_capabilities=(
                policy_snapshot.platform_capabilities
                if policy_snapshot is not None
                else None
            ),
            requested_tools=(
                policy_snapshot.allowed_platform_tools
                if policy_snapshot is not None
                else None if request.tools_enabled else frozenset()
            ),
            requested_skills=(
                policy_snapshot.allowed_skills
                if policy_snapshot is not None
                else None
            ),
            requested_native_capabilities=(
                policy_snapshot.runtime_native_capabilities
                if policy_snapshot is not None
                else None
            ),
            parent=request.parent_policy,
            artifact_ids=(
                policy_snapshot.artifact_ids
                if policy_snapshot is not None
                else frozenset(
                    attachment.artifact_id for attachment in request.attachments
                )
            ),
        )
        binding = await self._ensure_binding(
            request.scope,
            policy.agent_id,
            policy.agent_definition_digest,
        )
        if self._policy_observer is not None:
            await self._policy_observer(request, policy)
        async with self._manager.lease(binding.agent_id) as runtime:
            session = await runtime.resume_session(
                ResumeRuntimeSession(
                    operation_id=f"resume:{request.turn_id}",
                    session=binding.runtime_session(),
                )
            )
            capabilities = frozenset(
                capability
                for capability in principal_capabilities
                if capability.value in policy.platform_capabilities
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
                tool_names=policy.allowed_platform_tools,
                binding_epoch=session.binding_epoch,
                ttl_seconds=min(300.0, policy.limits.deadline_seconds),
                allow_tools=bool(policy.allowed_platform_tools),
            )
            external = (
                resolver.runtime_spec(binding.agent_id).implementation == "codex"
            )
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
                turn_context = (
                    await self._context_provider(
                        request.scope,
                        request.input,
                        frozenset(self._gateway.authorized_tool_names(grant)),
                        policy.allowed_skills,
                    )
                    if self._context_provider is not None
                    else RuntimeTurnContext()
                )
                turn = await runtime.start_turn(
                    RuntimeTurnRequest(
                        session=session,
                        operation_id=request.turn_id,
                        input=self._input_parts(request),
                        mcp=endpoint,
                        context=turn_context,
                        options={
                            "native_capabilities": ",".join(
                                sorted(policy.runtime_native_capabilities)
                            )
                        },
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
                            if (
                                event.kind not in {"user_input", "mcp_elicitation"}
                                or request.interaction is None
                            ):
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

    @asynccontextmanager
    async def _session_turn_lease(
        self,
        scope: RuntimeScope,
    ) -> AsyncIterator[None]:
        key = (scope.principal_id, scope.session_handle)
        async with self._turn_leases_guard:
            lease = self._turn_leases.get(key)
            if lease is None:
                lease = _SessionTurnLease(lock=asyncio.Lock())
                self._turn_leases[key] = lease
            lease.users += 1
        try:
            async with lease.lock:
                yield
        finally:
            async with self._turn_leases_guard:
                lease.users -= 1
                if lease.users == 0:
                    self._turn_leases.pop(key, None)

    async def health(self, agent_id: str | None = None):
        return await self._manager.health(
            self._manager.resolve_agent_id(agent_id)
        )

    async def _ensure_binding(
        self,
        scope: RuntimeScope,
        requested_agent_id: str,
        agent_config_digest: str,
    ) -> AgentSessionBinding:
        existing = await asyncio.to_thread(self._bindings.get, scope)
        if existing is not None:
            selected = self._manager.resolve_agent_id(requested_agent_id)
            if existing.agent_id != selected:
                raise ValueError("Session is already bound to a different Agent")
            if existing.agent_config_digest == agent_config_digest:
                return existing
        lock = await self._binding_lock(scope.session_handle)
        async with lock:
            existing = await asyncio.to_thread(self._bindings.get, scope)
            if existing is not None:
                if existing.agent_id != requested_agent_id:
                    raise ValueError("Session is already bound to a different Agent")
                if existing.agent_config_digest == agent_config_digest:
                    return existing
                binding_epoch = existing.binding_epoch + 1
            else:
                binding_epoch = 1
            agent_id = self._manager.resolve_agent_id(requested_agent_id)
            async with self._manager.lease(agent_id) as runtime:
                session = await runtime.create_session(
                    CreateRuntimeSession(
                        operation_id=(
                            f"bind:{scope.session_handle}:{agent_id}:{binding_epoch}"
                        ),
                        binding_epoch=binding_epoch,
                    )
                )
            if existing is not None:
                return await asyncio.to_thread(
                    self._bindings.rebind,
                    scope,
                    session,
                    agent_config_digest=agent_config_digest,
                    expected_revision=existing.revision,
                )
            return await asyncio.to_thread(
                self._bindings.create,
                scope,
                session,
                agent_config_digest=agent_config_digest,
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
