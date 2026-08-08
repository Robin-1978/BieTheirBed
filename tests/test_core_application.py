from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

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
from pc_assistant.agent_runtime.core_application import CoreApplication
from pc_assistant.agent_runtime.run_registry import CoreRunRegistry
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.exceptions import SessionNotFoundError


class FakeRuntime:
    def __init__(self) -> None:
        self.cancel_requests: list[tuple[RuntimeScope, CancelRequest]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False
        self.block = False

    def run(
        self,
        context: RuntimeRunContext,
        request: RunRequest,
    ) -> AsyncIterator[RuntimeEvent]:
        async def stream() -> AsyncIterator[RuntimeEvent]:
            self.started.set()
            if self.block:
                await self.release.wait()
            if self.fail:
                raise RuntimeError("provider secret detail")
            yield RuntimeEvent(
                event_type="content_delta",
                payload=RuntimeEventPayload(content=request.input),
            )

        return stream()

    async def cancel(self, scope: RuntimeScope, request: CancelRequest) -> CancelResult:
        self.cancel_requests.append((scope, request))
        self.release.set()
        return CancelResult(accepted=True, status="cancelling")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


def _application(tmp_path: Path):
    sessions = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "session-opaque",
    )
    scope = sessions.create("principal-a")
    runtime = FakeRuntime()
    runs = CoreRunRegistry(run_id_factory=lambda: "run-opaque")
    return CoreApplication(runtime, sessions, runs), runtime, runs, scope


@pytest.mark.asyncio
async def test_successful_run_has_ordered_single_terminal_event(tmp_path: Path) -> None:
    application, _runtime, runs, scope = _application(tmp_path)
    request = RunRequest(client_request_id="request-a", input="hello")

    events = [
        event
        async for event in application.run("principal-a", scope.session_handle, request)
    ]

    assert [event.event_type for event in events] == [
        "run_started",
        "content_delta",
        "completed",
    ]
    assert [event.event_seq for event in events] == [1, 2, 3]
    assert sum(event.is_terminal for event in events) == 1
    assert runs.status("principal-a", "run-opaque") is None


@pytest.mark.asyncio
async def test_runtime_failure_is_redacted_and_has_one_failed_terminal(tmp_path: Path) -> None:
    application, runtime, runs, scope = _application(tmp_path)
    runtime.fail = True
    request = RunRequest(client_request_id="request-a", input="hello")

    events = [
        event
        async for event in application.run("principal-a", scope.session_handle, request)
    ]

    assert [event.event_type for event in events] == ["run_started", "failed"]
    assert events[-1].payload.content == "Run failed"
    assert "secret" not in str(events)
    assert runs.status("principal-a", "run-opaque") is None


@pytest.mark.asyncio
async def test_cancel_is_bound_to_principal_and_run(tmp_path: Path) -> None:
    application, runtime, runs, scope = _application(tmp_path)
    runtime.block = True
    request = RunRequest(client_request_id="request-a", input="hello")
    received = []

    async def consume() -> None:
        async for event in application.run("principal-a", scope.session_handle, request):
            received.append(event)

    task = asyncio.create_task(consume())
    await runtime.started.wait()

    foreign = await application.cancel(
        "principal-b",
        CancelRequest(run_id="run-opaque"),
    )
    own = await application.cancel(
        "principal-a",
        CancelRequest(run_id="run-opaque"),
    )
    await task

    assert foreign == CancelResult(accepted=False, status="not_found")
    assert own == CancelResult(accepted=True, status="cancelling")
    assert len(runtime.cancel_requests) == 1
    assert runtime.cancel_requests[0][0] == scope
    assert [event.event_type for event in received] == ["run_started", "cancelled"]
    assert sum(event.is_terminal for event in received) == 1
    assert runs.status("principal-a", "run-opaque") is None


@pytest.mark.asyncio
async def test_run_rejects_foreign_session_before_runtime_start(tmp_path: Path) -> None:
    application, runtime, _runs, scope = _application(tmp_path)
    request = RunRequest(client_request_id="request-a", input="hello")

    with pytest.raises(SessionNotFoundError, match="Session not found"):
        [
            event
            async for event in application.run(
                "principal-b",
                scope.session_handle,
                request,
            )
        ]

    assert not runtime.started.is_set()
