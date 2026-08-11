from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import (
    CancelResult,
    HealthStatus,
    RunRequest,
    RuntimeEvent,
    RuntimeEventPayload,
    RuntimeRunContext,
)
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.conversation import ChatTurnState, ConversationRepository, ConversationService


class ChunkRuntime:
    def __init__(self, chunks: int = 1000, *, block: bool = False) -> None:
        self.chunks = chunks
        self.block = block
        self.started = asyncio.Event()

    async def run(self, context: RuntimeRunContext, request: RunRequest):
        del request
        self.started.set()
        if self.block:
            await context.cancellation.wait()
            return
        for _ in range(self.chunks):
            yield RuntimeEvent(
                event_type="reasoning_delta",
                payload=RuntimeEventPayload(content="r"),
            )
            yield RuntimeEvent(
                event_type="content_delta",
                payload=RuntimeEventPayload(content="x"),
            )
        yield RuntimeEvent(
            event_type="final_output",
            payload=RuntimeEventPayload(content="done"),
        )

    async def cancel(self, scope, request):
        del scope, request
        return CancelResult(accepted=False, status="not_found")

    async def health_check(self):
        return HealthStatus(healthy=True)


class SerialRuntime(ChunkRuntime):
    def __init__(self) -> None:
        super().__init__(chunks=0)
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def run(self, context: RuntimeRunContext, request: RunRequest):
        del context, request
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            yield RuntimeEvent(
                event_type="final_output",
                payload=RuntimeEventPayload(content="done"),
            )
        finally:
            self.active -= 1


class ProgressRuntime(ChunkRuntime):
    def __init__(self) -> None:
        super().__init__(chunks=0)

    async def run(self, context: RuntimeRunContext, request: RunRequest):
        del context, request
        self.started.set()
        yield RuntimeEvent(
            event_type="reasoning_delta",
            payload=RuntimeEventPayload(content="先检查状态", iteration=1),
        )
        await asyncio.sleep(0.08)
        yield RuntimeEvent(
            event_type="tool_call",
            payload=RuntimeEventPayload(
                tool_call_id="call-a",
                tool_name="status",
                iteration=1,
            ),
        )
        await asyncio.sleep(0.08)
        yield RuntimeEvent(
            event_type="tool_result",
            payload=RuntimeEventPayload(
                tool_call_id="call-a",
                tool_name="status",
                tool_result={"ok": True},
                iteration=1,
            ),
        )
        await asyncio.sleep(0.08)
        yield RuntimeEvent(
            event_type="content_delta",
            payload=RuntimeEventPayload(content="检查完成", iteration=2),
        )
        yield RuntimeEvent(
            event_type="final_output",
            payload=RuntimeEventPayload(content="检查完成", iteration=2),
        )


def _service(tmp_path: Path, runtime: ChunkRuntime):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(database, turn_id_factory=lambda: "turn-a")
    return database, scope, repository, ConversationService(sessions, repository, runtime)


@pytest.mark.asyncio
async def test_stream_coalesces_chunks_without_persisting_events(tmp_path: Path) -> None:
    database, scope, _repository, service = _service(tmp_path, ChunkRuntime())
    await service.start()
    turn = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="hello",
    )

    snapshots = [signal async for signal in service.updates(scope.principal_id, turn.turn_id)]
    completed = snapshots[-1].turn
    assert completed.state is ChatTurnState.COMPLETED
    assert completed.reasoning == "r" * 1000
    assert completed.content == "x" * 1000
    assert completed.final_output == "done"
    assert [entry.kind for entry in completed.timeline] == ["reasoning", "content"]
    assert all(
        current.turn.revision < following.turn.revision
        for current, following in zip(snapshots, snapshots[1:])
    )
    assert len(snapshots) < 50

    persisted = _repository.get(scope.principal_id, turn.turn_id)
    assert persisted.timeline == completed.timeline

    with sqlite3.connect(database) as db:
        conversation_rows = db.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
        task_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_tasks'"
        ).fetchone()
    assert conversation_rows == 1
    assert task_table is None
    await service.stop()


@pytest.mark.asyncio
async def test_stream_publishes_monotonic_ordered_progress_snapshots(tmp_path: Path) -> None:
    _database, scope, repository, service = _service(tmp_path, ProgressRuntime())
    await service.start()
    turn = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="check",
    )

    snapshots = [signal.turn async for signal in service.updates(scope.principal_id, turn.turn_id)]

    assert len(snapshots) >= 4
    assert all(
        current.revision <= following.revision
        for current, following in zip(snapshots, snapshots[1:])
    )
    assert all(
        following.revision > current.revision
        for current, following in zip(snapshots, snapshots[1:])
        if following.timeline != current.timeline
        or following.state != current.state
        or following.final_output != current.final_output
    )
    assert [entry.kind for entry in snapshots[-1].timeline] == [
        "reasoning",
        "tool_call",
        "tool_result",
        "content",
    ]
    assert repository.get(scope.principal_id, turn.turn_id).timeline == snapshots[-1].timeline
    await service.stop()


@pytest.mark.asyncio
async def test_stop_marks_live_turn_cancelled(tmp_path: Path) -> None:
    runtime = ChunkRuntime(block=True)
    _database, scope, repository, service = _service(tmp_path, runtime)
    await service.start()
    turn = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="hello",
    )
    await runtime.started.wait()

    await service.stop()

    stored = repository.get(scope.principal_id, turn.turn_id)
    assert stored.state is ChatTurnState.CANCELLED
    assert stored.cancel_requested is True
    assert stored.finished_at is not None


@pytest.mark.asyncio
async def test_conversation_service_serializes_turns_for_one_session(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.db"
    turn_ids = iter(("turn-a", "turn-b"))
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(
        database,
        turn_id_factory=lambda: next(turn_ids),
    )
    runtime = SerialRuntime()
    service = ConversationService(sessions, repository, runtime)
    await service.start()
    first = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="first",
    )
    await runtime.started.wait()
    second = await service.create_turn(
        scope,
        client_request_id="request-b",
        user_input="second",
    )
    await asyncio.sleep(0)

    assert runtime.max_active == 1
    runtime.release.set()
    await asyncio.gather(*tuple(service._executions.values()))
    assert runtime.max_active == 1
    assert repository.get(scope.principal_id, first.turn_id).state is ChatTurnState.COMPLETED
    assert repository.get(scope.principal_id, second.turn_id).state is ChatTurnState.COMPLETED
    await service.stop()
