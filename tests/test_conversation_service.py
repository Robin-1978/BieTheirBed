from __future__ import annotations

import asyncio
import sqlite3
from itertools import pairwise
from pathlib import Path

import pytest

from knoa_agent_contracts import (
    AssistantDelta,
    ReasoningSummaryDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.conversation import (
    ChatTurnState,
    ConversationRepository,
    ConversationService,
)


def _base(request):
    return {
        "runtime_session_ref": "agent-session-a",
        "runtime_turn_ref": request.turn_id,
        "occurred_at": 1.0,
    }


class ChunkRuntime:
    def __init__(self, chunks: int = 1000, *, block: bool = False) -> None:
        self.chunks = chunks
        self.block = block
        self.started = asyncio.Event()

    async def execute_turn(self, request):
        self.started.set()
        if self.block:
            await request.cancellation.wait()
            yield TurnFinished(
                **_base(request),
                status="interrupted",
                error_code="cancelled",
            )
            return
        for _ in range(self.chunks):
            yield ReasoningSummaryDelta(**_base(request), content="r")
            yield AssistantDelta(**_base(request), content="x")
        yield TurnFinished(
            **_base(request),
            status="completed",
            final_output="done",
        )


class ProgressRuntime(ChunkRuntime):
    def __init__(self) -> None:
        super().__init__(chunks=0)

    async def execute_turn(self, request):
        self.started.set()
        yield ReasoningSummaryDelta(**_base(request), content="先检查状态")
        await asyncio.sleep(0.08)
        yield ToolCallStarted(
            **_base(request),
            tool_call_id="call-a",
            tool_name="status",
        )
        await asyncio.sleep(0.08)
        yield ToolCallFinished(
            **_base(request),
            tool_call_id="call-a",
            tool_name="status",
            status="completed",
            output={"ok": True},
        )
        await asyncio.sleep(0.08)
        yield AssistantDelta(**_base(request), content="检查完成")
        yield TurnFinished(
            **_base(request),
            status="completed",
            final_output="检查完成",
        )


class ArtifactRuntime(ChunkRuntime):
    def __init__(self) -> None:
        super().__init__(chunks=0)

    async def execute_turn(self, request):
        self.started.set()
        artifact = {
            "artifact_id": "artifact-a",
            "kind": "image",
            "name": "screenshot.jpg",
            "media_type": "image/jpeg",
            "size": 123,
            "direction": "outbound",
            "ownership": "generated",
            "retention": "temporary",
            "status": "available",
            "visibility": "user",
        }
        for call_id in ("call-a", "call-b"):
            yield ToolCallFinished(
                **_base(request),
                tool_call_id=call_id,
                tool_name="screenshot",
                status="completed",
                output={"success": True, "artifact": artifact},
            )
        yield ToolCallFinished(
            **_base(request),
            tool_call_id="call-c",
            tool_name="status",
            status="completed",
            output={"artifact": {"artifact_id": "invalid"}},
        )
        yield TurnFinished(
            **_base(request),
            status="completed",
            final_output="done",
        )


class ApprovalRuntime(ChunkRuntime):
    def __init__(self) -> None:
        super().__init__(chunks=0)
        self.tool_executed = False

    async def execute_turn(self, request):
        self.started.set()
        approved = await request.confirmation.confirm(
            request.scope,
            request.turn_id,
            ProposedToolCall(
                call_id="call-a",
                name="write_file",
                arguments={"path": "out.txt"},
            ),
            "local_write:medium",
        )
        if approved:
            self.tool_executed = True
        yield TurnFinished(
            **_base(request),
            status="completed",
            final_output="done",
        )


class TransientFailureRuntime(ChunkRuntime):
    def __init__(self) -> None:
        super().__init__(chunks=0)
        self.requests = []

    async def execute_turn(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield TurnFinished(
                **_base(request),
                status="failed",
                error_code="runtime_failed",
            )
            return
        yield AssistantDelta(**_base(request), content="你好！")
        yield TurnFinished(
            **_base(request),
            status="completed",
            final_output="你好！",
        )


def _service(tmp_path: Path, runtime: ChunkRuntime):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(database, turn_id_factory=lambda: "turn-a")
    return database, scope, repository, ConversationService(sessions, repository, runtime)


@pytest.mark.asyncio
async def test_transient_empty_runtime_failure_is_retried_once(tmp_path: Path) -> None:
    runtime = TransientFailureRuntime()
    _database, scope, repository, service = _service(tmp_path, runtime)
    await service.start()
    turn = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="你好",
    )

    snapshots = [signal.turn async for signal in service.updates(scope.principal_id, turn.turn_id)]

    assert snapshots[-1].state is ChatTurnState.COMPLETED
    assert snapshots[-1].final_output == "你好！"
    assert len(runtime.requests) == 2
    assert runtime.requests[0].operation_id == ""
    assert runtime.requests[1].operation_id == "turn-a:recovery:1"
    assert repository.get(scope.principal_id, turn.turn_id).failure_code == ""
    await service.stop()


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
        for current, following in pairwise(snapshots)
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
        for current, following in pairwise(snapshots)
    )
    assert all(
        following.revision > current.revision
        for current, following in pairwise(snapshots)
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
async def test_tool_output_artifact_is_persisted_once_on_chat_turn(
    tmp_path: Path,
) -> None:
    _database, scope, repository, service = _service(tmp_path, ArtifactRuntime())
    await service.start()
    turn = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="capture",
    )

    snapshots = [signal.turn async for signal in service.updates(scope.principal_id, turn.turn_id)]

    completed = snapshots[-1]
    assert [artifact.artifact_id for artifact in completed.artifacts] == ["artifact-a"]
    persisted = repository.get(scope.principal_id, turn.turn_id)
    assert [artifact.artifact_id for artifact in persisted.artifacts] == ["artifact-a"]
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
async def test_cancel_expires_waiting_approval_and_never_executes_tool(
    tmp_path: Path,
) -> None:
    runtime = ApprovalRuntime()
    _database, scope, repository, service = _service(tmp_path, runtime)
    await service.start()
    turn = await service.create_turn(
        scope,
        client_request_id="request-a",
        user_input="write a file",
    )

    async with asyncio.timeout(2):
        while True:
            waiting = repository.get(scope.principal_id, turn.turn_id)
            if waiting.state is ChatTurnState.WAITING_APPROVAL:
                break
            await asyncio.sleep(0.01)

    await service.cancel(scope.principal_id, turn.turn_id)

    async with asyncio.timeout(2):
        while True:
            stored = repository.get(scope.principal_id, turn.turn_id)
            if stored.state is ChatTurnState.CANCELLED:
                break
            await asyncio.sleep(0.01)

    assert stored.cancel_requested is True
    assert stored.finished_at is not None
    assert len(stored.approvals) == 1
    assert stored.approvals[0].state == "expired"
    assert stored.approvals[0].resolved_by == "turn_cancelled"
    assert runtime.tool_executed is False
    await service.stop()
