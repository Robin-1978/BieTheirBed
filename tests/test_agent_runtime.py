from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import (
    ArtifactAttachment,
    CancelRequest,
    HealthStatus,
    RunRequest,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeRunContext,
)
from pc_assistant.agent_runtime.react_loop import ReActEvent, ReActOutcome
from pc_assistant.agent_runtime.runtime import AgentRuntime, ArtifactMessageHydrator
from pc_assistant.agent_runtime.session_store import (
    RuntimeSessionRepository,
    SessionSnapshot,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import current_memory_scope
from pc_assistant.tools.registry import ToolRegistry


DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class FakeLoop:
    def __init__(
        self,
        status: str = "completed",
        *,
        wait_for_cancel: bool = False,
        final_content: str = "done",
    ):
        self.status = status
        self.wait_for_cancel = wait_for_cancel
        self.final_content = final_content
        self.started = asyncio.Event()
        self.contexts = []
        self.memory_scopes = []
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()

    async def run(self, context):
        self.contexts.append(context)
        self.memory_scopes.append(current_memory_scope())
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.wait_for_cancel:
                await context.cancellation.wait()
            elif not self.release.is_set():
                await self.release.wait()
            if self.status == "completed":
                yield ReActEvent(
                    event_type="runtime_event",
                    runtime_event=RuntimeEvent(
                        event_type="content_delta",
                        payload=RuntimeEventPayload(content="done"),
                    ),
                )
                messages = (
                    *context.messages,
                    {"role": "assistant", "content": self.final_content},
                )
            else:
                messages = context.messages
            yield ReActEvent(
                event_type="outcome",
                outcome=ReActOutcome(
                    status=self.status,
                    messages=messages,
                    final_content=(
                        self.final_content if self.status == "completed" else ""
                    ),
                    error_code="provider_failed" if self.status == "failed" else "",
                ),
            )
        finally:
            self.active -= 1


async def _healthy() -> HealthStatus:
    return HealthStatus(healthy=True)


def _runtime(tmp_path: Path, loop: FakeLoop, *, run_observer=None):
    sessions = RuntimeSessionRepository(tmp_path / "assistant.db")
    artifacts = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "assistant.db",
    )
    runtime = AgentRuntime(
        loop,
        ToolRegistry(),
        artifacts,
        capabilities_for=lambda _scope: frozenset(),
        health_probe=_healthy,
        system_prompt="system",
        run_observer=run_observer,
    )
    return runtime, sessions, artifacts


def _context(
    scope,
    run_id: str,
    sessions: RuntimeSessionRepository,
) -> RuntimeRunContext:
    snapshot = sessions.load(scope)

    async def commit(messages) -> None:
        sessions.save(scope, SessionSnapshot(messages=messages))

    return RuntimeRunContext(
        scope=scope,
        run_id=run_id,
        cancellation=asyncio.Event(),
        messages=snapshot.messages,
        commit_messages=commit,
    )


@pytest.mark.asyncio
async def test_completed_turn_commits_scoped_transcript_and_events(tmp_path: Path) -> None:
    loop = FakeLoop()
    loop.release.set()
    runtime, sessions, _artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")

    events = [
        event
        async for event in runtime.run(
            _context(scope, "run-a", sessions),
            RunRequest(client_request_id="request-a", input="hello"),
        )
    ]

    assert [event.event_type for event in events] == [
        "content_delta",
        "final_output",
    ]
    assert [event.payload.content for event in events] == ["done", "done"]
    assert [message["role"] for message in sessions.load(scope).messages] == [
        "user",
        "assistant",
    ]
    assert loop.memory_scopes[0].principal_id == "principal-a"
    assert loop.memory_scopes[0].session_id == scope.session_handle


@pytest.mark.asyncio
async def test_completed_turn_is_observed_only_after_transcript_commit(
    tmp_path: Path,
) -> None:
    loop = FakeLoop()
    loop.release.set()
    observed = []
    sessions_ref = None

    async def observe(scope, _run_id, _request, outcome, _elapsed_ms):
        observed.append((outcome, sessions_ref.load(scope)))

    runtime, sessions, _artifacts = _runtime(
        tmp_path,
        loop,
        run_observer=observe,
    )
    sessions_ref = sessions
    scope = sessions.create("principal-a")

    async for _event in runtime.run(
        _context(scope, "run-a", sessions),
        RunRequest(client_request_id="request-a", input="hello"),
    ):
        pass

    assert len(observed) == 1
    outcome, snapshot = observed[0]
    assert outcome.status == "completed"
    assert [message["role"] for message in snapshot.messages] == [
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_transcript_commit_failure_is_observed_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = FakeLoop()
    loop.release.set()
    observed = []

    async def observe(_scope, _run_id, _request, outcome, _elapsed_ms):
        observed.append(outcome)

    runtime, sessions, _artifacts = _runtime(
        tmp_path,
        loop,
        run_observer=observe,
    )
    scope = sessions.create("principal-a")

    def fail_save(_scope, _snapshot):
        raise OSError("disk full")

    monkeypatch.setattr(sessions, "save", fail_save)

    with pytest.raises(OSError, match="disk full"):
        async for _event in runtime.run(
            _context(scope, "run-a", sessions),
            RunRequest(client_request_id="request-a", input="hello"),
        ):
            pass

    assert len(observed) == 1
    assert observed[0].status == "failed"
    assert observed[0].error_code == "transcript_persistence_failed"


@pytest.mark.asyncio
async def test_failed_turn_rolls_back_to_snapshot(tmp_path: Path) -> None:
    loop = FakeLoop("failed")
    loop.release.set()
    runtime, sessions, _artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")
    original = SessionSnapshot(
        messages=({"role": "user", "content": "existing"},),
    )
    sessions.save(scope, original)

    with pytest.raises(RuntimeError, match="provider_failed"):
        async for _event in runtime.run(
            _context(scope, "run-a", sessions),
            RunRequest(client_request_id="request-a", input="new"),
        ):
            pass

    assert sessions.load(scope) == original


@pytest.mark.asyncio
async def test_cancel_targets_active_run_and_does_not_persist(tmp_path: Path) -> None:
    loop = FakeLoop("cancelled", wait_for_cancel=True)
    runtime, sessions, _artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")
    context = _context(scope, "run-a", sessions)

    async def consume() -> None:
        async for _event in runtime.run(
            context,
            RunRequest(client_request_id="request-a", input="hello"),
        ):
            pass

    task = asyncio.create_task(consume())
    await loop.started.wait()
    result = await runtime.cancel(scope, CancelRequest(run_id="run-a"))
    await task

    assert result.accepted and result.status == "cancelling"
    assert context.cancellation.is_set()
    assert sessions.load(scope).messages == ()


@pytest.mark.asyncio
async def test_pre_start_cancellation_is_observed_as_cancelled(tmp_path: Path) -> None:
    loop = FakeLoop()
    observed = []

    async def observe(_scope, _run_id, _request, outcome, _elapsed_ms):
        observed.append(outcome)

    runtime, sessions, _artifacts = _runtime(
        tmp_path,
        loop,
        run_observer=observe,
    )
    scope = sessions.create("principal-a")
    context = _context(scope, "run-a", sessions)
    context.cancellation.set()

    events = [
        event
        async for event in runtime.run(
            context,
            RunRequest(client_request_id="request-a", input="hello"),
        )
    ]

    assert events == []
    assert len(observed) == 1
    assert observed[0].status == "cancelled"
    assert observed[0].error_code == "cancelled"


@pytest.mark.asyncio
async def test_pre_react_failure_is_observed_as_failed(tmp_path: Path) -> None:
    loop = FakeLoop()
    observed = []

    async def observe(_scope, _run_id, _request, outcome, _elapsed_ms):
        observed.append(outcome)

    async def fail_context(_scope, _input):
        raise RuntimeError("context unavailable")

    sessions = RuntimeSessionRepository(tmp_path / "assistant.db")
    artifacts = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "assistant.db",
    )
    runtime = AgentRuntime(
        loop,
        ToolRegistry(),
        artifacts,
        capabilities_for=lambda _scope: frozenset(),
        health_probe=_healthy,
        system_prompt="system",
        runtime_context=fail_context,
        run_observer=observe,
    )
    scope = sessions.create("principal-a")

    with pytest.raises(RuntimeError, match="context unavailable"):
        async for _event in runtime.run(
            _context(scope, "run-a", sessions),
            RunRequest(client_request_id="request-a", input="hello"),
        ):
            pass

    assert len(observed) == 1
    assert observed[0].status == "failed"
    assert observed[0].error_code == "runtime_failed"


@pytest.mark.asyncio
async def test_runtime_does_not_own_product_session_serialization(tmp_path: Path) -> None:
    loop = FakeLoop()
    runtime, sessions, _artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")

    async def consume(run_id: str) -> None:
        async for _event in runtime.run(
            _context(scope, run_id, sessions),
            RunRequest(client_request_id=f"request-{run_id}", input=run_id),
        ):
            pass

    first = asyncio.create_task(consume("run-a"))
    await loop.started.wait()
    second = asyncio.create_task(consume("run-b"))
    await asyncio.sleep(0)

    assert loop.max_active == 2
    loop.release.set()
    await asyncio.gather(first, second)
    assert loop.max_active == 2


@pytest.mark.asyncio
async def test_attachment_history_stores_reference_and_hydration_is_ephemeral(
    tmp_path: Path,
) -> None:
    loop = FakeLoop()
    loop.release.set()
    runtime, sessions, artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")
    raw_ref = artifacts.put_data_url(scope.session_handle, DATA_URL)

    async for _event in runtime.run(
        _context(scope, "run-a", sessions),
        RunRequest(
            client_request_id="request-a",
            attachments=(ArtifactAttachment(artifact_id=raw_ref["artifact_id"]),),
        ),
    ):
        pass

    messages = sessions.load(scope).messages
    assert "image_ref" in str(messages[0])
    assert "base64" not in str(messages)
    hydrated = await ArtifactMessageHydrator(artifacts).hydrate(scope, list(messages))
    assert "data:image/png;base64" in str(hydrated[0])
    assert "base64" not in str(messages)


@pytest.mark.asyncio
async def test_long_final_output_creates_persistent_markdown_artifact(
    tmp_path: Path,
) -> None:
    final_content = "完整报告\n\n" + ("详细内容。" * 3_000)
    loop = FakeLoop(final_content=final_content)
    loop.release.set()
    runtime, sessions, artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")

    events = [
        event
        async for event in runtime.run(
            _context(scope, "run-long", sessions),
            RunRequest(client_request_id="request-long", input="生成报告"),
        )
    ]

    artifact_event = next(event for event in events if event.event_type == "artifact")
    assert artifact_event.payload.artifact is not None
    artifact = artifact_event.payload.artifact
    assert artifact.name == "run-long-result.md"
    assert artifact.kind == "file"
    assert artifact.retention == "persistent"
    downloaded = artifacts.read_data_url(
        scope.session_handle,
        artifact.artifact_id,
        max_bytes=100_000,
    )
    assert downloaded.startswith("data:text/markdown;base64,")
    assert events[-1].event_type == "final_output"
    assert events[-1].payload.content == final_content


@pytest.mark.asyncio
async def test_long_result_artifact_failure_keeps_full_final_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    final_content = "内容" * 7_000
    loop = FakeLoop(final_content=final_content)
    loop.release.set()
    runtime, sessions, artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(artifacts, "create_generated_text", fail)
    events = [
        event
        async for event in runtime.run(
            _context(scope, "run-fallback", sessions),
            RunRequest(client_request_id="request-fallback", input="生成报告"),
        )
    ]

    assert "artifact" not in [event.event_type for event in events]
    assert events[-1].event_type == "final_output"
    assert events[-1].payload.content == final_content


@pytest.mark.asyncio
async def test_run_confirmation_is_forwarded_to_react_context(tmp_path: Path) -> None:
    loop = FakeLoop()
    loop.release.set()
    runtime, sessions, _artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")

    class Confirmation:
        async def confirm(self, scope, run_id, call, reason):
            del scope, run_id, call, reason
            return True

    confirmation = Confirmation()
    context = RuntimeRunContext(
        scope=scope,
        run_id="run-a",
        cancellation=asyncio.Event(),
        confirmation=confirmation,
    )

    async for _event in runtime.run(
        context,
        RunRequest(client_request_id="request-a", input="hello"),
    ):
        pass

    assert loop.contexts[0].confirmation is confirmation


@pytest.mark.asyncio
async def test_run_can_disable_all_tools_without_mutating_registry(tmp_path: Path) -> None:
    loop = FakeLoop()
    loop.release.set()
    runtime, sessions, _artifacts = _runtime(tmp_path, loop)
    scope = sessions.create("principal-a")

    async for _event in runtime.run(
        _context(scope, "run-a", sessions),
        RunRequest(
            client_request_id="request-a",
            input="hello",
            tools_enabled=False,
        ),
    ):
        pass

    assert loop.contexts[0].capabilities == frozenset()
    assert loop.contexts[0].tool_definitions == ()
