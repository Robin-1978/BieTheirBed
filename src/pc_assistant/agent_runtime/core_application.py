"""Transport-neutral Core application entrypoint."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from pc_assistant.agent_runtime.contracts import (
    AgentRuntimePort,
    CancelRequest,
    CancelResult,
    HealthStatus,
    RunEvent,
    RunRequest,
    RuntimeEventPayload,
    RuntimeRunContext,
)
from pc_assistant.agent_runtime.events import RunEventSequencer
from pc_assistant.agent_runtime.run_registry import CoreRunRegistry, TerminalRunStatus
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository

if TYPE_CHECKING:
    from pc_assistant.agent_runtime.tool_step import ConfirmationPort


class CoreApplication:
    """Bind trusted principal identity to the runtime and public event stream."""

    def __init__(
        self,
        runtime: AgentRuntimePort,
        sessions: RuntimeSessionRepository,
        runs: CoreRunRegistry,
    ) -> None:
        self._runtime = runtime
        self._sessions = sessions
        self._runs = runs

    async def health_check(self) -> HealthStatus:
        return await self._runtime.health_check()

    async def run(
        self,
        principal_id: str,
        session_handle: str,
        request: RunRequest,
        *,
        confirmation: ConfirmationPort | None = None,
    ) -> AsyncIterator[RunEvent]:
        scope = await asyncio.to_thread(
            self._sessions.resolve,
            principal_id,
            session_handle,
        )
        handle = self._runs.start(scope)
        events = RunEventSequencer(handle.run_id)
        yield events.emit("run_started")
        terminal_status: TerminalRunStatus | None = None

        try:
            async for event in self._runtime.run(
                RuntimeRunContext(
                    scope=scope,
                    run_id=handle.run_id,
                    cancellation=handle.cancellation,
                    confirmation=confirmation,
                ),
                request,
            ):
                if handle.cancel_requested:
                    break
                yield events.emit(event.event_type, event.payload)
            if handle.cancel_requested:
                self._runs.finish(handle, "cancelled")
                terminal_status = "cancelled"
                yield events.emit(
                    "cancelled",
                    RuntimeEventPayload(content="Run cancelled"),
                )
            else:
                self._runs.finish(handle, "completed")
                terminal_status = "completed"
                yield events.emit("completed")
        except asyncio.CancelledError:
            self._runs.finish(handle, "cancelled")
            terminal_status = "cancelled"
            raise
        except Exception:
            self._runs.finish(handle, "failed")
            terminal_status = "failed"
            yield events.emit(
                "failed",
                RuntimeEventPayload(content="Run failed"),
            )
        finally:
            if terminal_status is None:
                terminal_status = "cancelled" if handle.cancel_requested else "failed"
                self._runs.finish(handle, terminal_status)
            self._runs.forget(handle)

    async def cancel(
        self,
        principal_id: str,
        request: CancelRequest,
    ) -> CancelResult:
        handle = self._runs.resolve(principal_id, request.run_id)
        if handle is None:
            return CancelResult(accepted=False, status="not_found")
        result = self._runs.request_cancel(principal_id, request.run_id)
        if result.status == "cancelling":
            await self._runtime.cancel(
                handle.scope,
                request,
            )
        return result
