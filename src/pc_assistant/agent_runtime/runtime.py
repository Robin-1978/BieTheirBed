"""Target-state principal-scoped AgentRuntime."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pc_assistant.agent_runtime.contracts import (
    CancelRequest,
    CancelResult,
    HealthStatus,
    RunRequest,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeRunContext,
    RuntimeScope,
)
from pc_assistant.agent_runtime.model_step import MessageHydratorPort
from pc_assistant.agent_runtime.react_loop import ReActContext, ReActLoop, ReActOutcome
from pc_assistant.agent_runtime.session_store import (
    RuntimeSessionRepository,
    SessionSnapshot,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import (
    MemoryScope,
    reset_memory_scope,
    set_memory_scope,
)
from pc_assistant.tools.base import ToolCapability
from pc_assistant.tools.registry import ToolRegistry


class ArtifactMessageHydrator(MessageHydratorPort):
    """Hydrate only request-scoped references into ephemeral provider payloads."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def hydrate(
        self,
        scope: RuntimeScope,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._store.hydrate_messages,
            scope.session_handle,
            messages,
        )


@dataclass
class _SessionLockEntry:
    lock: asyncio.Lock
    users: int = 0


class AgentRuntime:
    """Own session serialization, transcript commit, and request-local scope."""

    def __init__(
        self,
        sessions: RuntimeSessionRepository,
        react_loop: ReActLoop,
        registry: ToolRegistry,
        artifacts: ArtifactStore,
        *,
        capabilities_for: Callable[[RuntimeScope], frozenset[ToolCapability]],
        health_probe: Callable[[], Awaitable[HealthStatus]],
        system_prompt: str,
        prompt_budget: int = 8192,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
        runtime_context: Callable[[RuntimeScope, str], Awaitable[str]] | None = None,
        run_observer: Callable[
            [RuntimeScope, str, RunRequest, ReActOutcome, float], Awaitable[None]
        ]
        | None = None,
        clock=time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._react_loop = react_loop
        self._registry = registry
        self._artifacts = artifacts
        self._capabilities_for = capabilities_for
        self._health_probe = health_probe
        self._system_prompt = system_prompt
        self._prompt_budget = prompt_budget
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._runtime_context = runtime_context
        self._run_observer = run_observer
        self._clock = clock
        self._session_locks: dict[str, _SessionLockEntry] = {}
        self._session_locks_guard = asyncio.Lock()
        self._active_runs: dict[str, RuntimeRunContext] = {}
        self._active_runs_guard = asyncio.Lock()

    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        return self._run(context, request)

    async def _run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        started = self._clock()
        scope = await asyncio.to_thread(
            self._sessions.resolve,
            context.scope.principal_id,
            context.scope.session_handle,
        )
        async with self._active_runs_guard:
            if context.run_id in self._active_runs:
                raise RuntimeError("Duplicate active run ID")
            self._active_runs[context.run_id] = context
        observed = False
        try:
            async with self._session_lease(scope.session_handle):
                if context.cancellation.is_set():
                    await self._observe_run(
                        scope,
                        context.run_id,
                        request,
                        ReActOutcome(
                            status="cancelled",
                            messages=(),
                            error_code="cancelled",
                        ),
                        started,
                    )
                    observed = True
                    return
                snapshot = await asyncio.to_thread(self._sessions.load, scope)
                user_message = await self._user_message(scope, request)
                messages = (*snapshot.messages, user_message)
                scope_token = set_memory_scope(
                    MemoryScope(
                        principal_id=scope.principal_id,
                        session_id=scope.session_handle,
                    )
                )
                try:
                    outcome: ReActOutcome | None = None
                    capabilities = (
                        self._capabilities_for(scope)
                        if request.tools_enabled
                        else frozenset()
                    )
                    runtime_context = (
                        await self._runtime_context(scope, request.input)
                        if self._runtime_context is not None
                        else ""
                    )
                    def current_tool_definitions() -> tuple[dict[str, Any], ...]:
                        return tuple(self._registry.definitions_for(capabilities))

                    async for event in self._react_loop.run(
                        ReActContext(
                            scope=scope,
                            client_request_id=request.client_request_id,
                            messages=messages,
                            tool_definitions=current_tool_definitions(),
                            tool_definition_provider=current_tool_definitions,
                            capabilities=capabilities,
                            cancellation=context.cancellation,
                            run_id=context.run_id,
                            confirmation=context.confirmation,
                            tool_commit=context.tool_commit,
                            system_prompt=self._system_prompt,
                            runtime_context=runtime_context,
                            prompt_budget=self._prompt_budget,
                            max_output_tokens=self._max_output_tokens,
                            temperature=self._temperature,
                        )
                    ):
                        if event.runtime_event is not None:
                            yield event.runtime_event
                        else:
                            outcome = event.outcome
                    if outcome is None:
                        raise RuntimeError("ReAct loop ended without an outcome")
                    if outcome.status == "completed":
                        try:
                            await asyncio.to_thread(
                                self._sessions.save,
                                scope,
                                SessionSnapshot(messages=outcome.messages),
                            )
                        except Exception:
                            await self._observe_run(
                                scope,
                                context.run_id,
                                request,
                                outcome.model_copy(
                                    update={
                                        "status": "failed",
                                        "error_code": "transcript_persistence_failed",
                                    }
                                ),
                                started,
                            )
                            observed = True
                            raise
                        yield RuntimeEvent(
                            event_type="final_output",
                            payload=RuntimeEventPayload(
                                content=outcome.final_content,
                                iteration=outcome.iterations,
                            ),
                        )
                    await self._observe_run(
                        scope,
                        context.run_id,
                        request,
                        outcome,
                        started,
                    )
                    observed = True
                    if outcome.status == "failed":
                        raise RuntimeError(outcome.error_code or "Agent run failed")
                finally:
                    reset_memory_scope(scope_token)
        except asyncio.CancelledError:
            if not observed:
                await self._observe_run(
                    scope,
                    context.run_id,
                    request,
                    ReActOutcome(
                        status="cancelled",
                        messages=(),
                        error_code="cancelled",
                    ),
                    started,
                )
            raise
        except Exception:
            if not observed:
                await self._observe_run(
                    scope,
                    context.run_id,
                    request,
                    ReActOutcome(
                        status="failed",
                        messages=(),
                        error_code="runtime_failed",
                    ),
                    started,
                )
            raise
        finally:
            async with self._active_runs_guard:
                self._active_runs.pop(context.run_id, None)

    async def cancel(
        self,
        scope: RuntimeScope,
        request: CancelRequest,
    ) -> CancelResult:
        owned = await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )
        async with self._active_runs_guard:
            context = self._active_runs.get(request.run_id)
            if context is None or context.scope != owned:
                return CancelResult(accepted=False, status="not_found")
            context.cancellation.set()
        return CancelResult(accepted=True, status="cancelling")

    async def health_check(self) -> HealthStatus:
        try:
            return await self._health_probe()
        except Exception:
            return HealthStatus(healthy=False, detail="Runtime health probe failed")

    async def _observe_run(
        self,
        scope: RuntimeScope,
        run_id: str,
        request: RunRequest,
        outcome: ReActOutcome,
        started: float,
    ) -> None:
        if self._run_observer is None:
            return
        try:
            await self._run_observer(
                scope,
                run_id,
                request,
                outcome,
                max(0.0, (self._clock() - started) * 1000),
            )
        except Exception:
            pass

    @asynccontextmanager
    async def _session_lease(
        self,
        session_handle: str,
    ) -> AsyncIterator[None]:
        async with self._session_locks_guard:
            entry = self._session_locks.setdefault(
                session_handle,
                _SessionLockEntry(lock=asyncio.Lock()),
            )
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._session_locks_guard:
                entry.users -= 1
                if entry.users == 0:
                    self._session_locks.pop(session_handle, None)

    async def _user_message(
        self,
        scope: RuntimeScope,
        request: RunRequest,
    ) -> dict[str, Any]:
        references: list[dict[str, Any]] = []
        for attachment in request.attachments:
            try:
                reference = await asyncio.to_thread(
                    self._artifacts.reference,
                    scope.session_handle,
                    attachment.artifact_id,
                    caption=attachment.caption,
                )
            except KeyError as exc:
                raise ValueError("Artifact not found") from exc
            references.append(reference)
        if not references:
            return {"role": "user", "content": request.input}
        content: list[dict[str, Any]] = []
        if request.input:
            content.append({"type": "text", "text": request.input})
        content.extend(references)
        return {"role": "user", "content": content}
