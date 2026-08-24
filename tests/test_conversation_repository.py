from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from knoa_platform.agent_runtime.contracts import ArtifactAttachment
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall, ToolStepResult
from knoa_platform.conversation import (
    ChatTimelineEntry,
    ChatTurnConflictError,
    ChatTurnState,
    ConversationRepository,
    ConversationSessionConflictError,
    ConversationSessionState,
)
from knoa_platform.tools.base import ToolEffect, ToolPolicy, ToolRisk


def _repository(tmp_path: Path):
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(
        database,
        turn_id_factory=lambda: "turn-a",
        approval_id_factory=lambda: "approval-a",
        clock=lambda: 1000.0,
    )
    return database, scope, repository


def test_turn_is_idempotent_and_scoped_to_a_session(tmp_path: Path) -> None:
    _database, scope, repository = _repository(tmp_path)
    attachment = ArtifactAttachment(artifact_id="artifact-a", caption="photo")

    created, was_created = repository.create(
        scope,
        client_request_id="request-a",
        user_input="hello",
        attachments=(attachment,),
    )
    repeated, repeated_created = repository.create(
        scope,
        client_request_id="request-a",
        user_input="hello",
        attachments=(attachment,),
    )

    assert was_created is True
    assert repeated_created is False
    assert repeated == created
    assert created.state is ChatTurnState.RUNNING
    assert repository.list_session("principal-a", "session-a") == ((created,), "")

    with pytest.raises(ChatTurnConflictError):
        repository.create(
            scope,
            client_request_id="request-a",
            user_input="different",
        )


def test_turn_persists_only_merged_snapshots_and_durable_side_effects(
    tmp_path: Path,
) -> None:
    database, scope, repository = _repository(tmp_path)
    turn, _ = repository.create(
        scope,
        client_request_id="request-a",
        user_input="hello",
    )
    call = ProposedToolCall(call_id="call-a", name="read_file", arguments={"path": "/tmp/a"})
    policy = ToolPolicy(
        effect=ToolEffect.READ_ONLY,
        capabilities=frozenset(),
        risk=ToolRisk.LOW,
    )

    step, created = repository.begin_tool_step(
        scope.principal_id,
        turn.turn_id,
        step_id="step-a",
        call=call,
        policy=policy,
    )
    assert created is True
    assert step.state == "running"
    repository.finish_tool_step(
        scope.principal_id,
        turn.turn_id,
        step.step_id,
        ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="completed",
            output={"content": "ok"},
        ),
    )
    approval, approval_created = repository.request_approval(
        scope.principal_id,
        turn.turn_id,
        step_id="step-b",
        call=ProposedToolCall(call_id="call-b", name="write_file", arguments={}),
        reason="writes a file",
    )
    assert approval_created is True
    resolved, changed, resolved_turn_id = repository.resolve_approval(
        scope.principal_id,
        approval.approval_id,
        approved=True,
        resolved_by="user",
    )
    assert changed is True
    assert resolved.state == "approved"
    assert resolved_turn_id == turn.turn_id

    completed = repository.checkpoint(
        scope.principal_id,
        turn.turn_id,
        state=ChatTurnState.COMPLETED,
        reasoning="checked",
        content="answer",
        final_output="answer",
        timeline=(
            ChatTimelineEntry(kind="reasoning", content="checked", iteration=1),
            ChatTimelineEntry(
                kind="tool_call",
                tool_call_id="call-a",
                tool_name="read_file",
                iteration=1,
            ),
            ChatTimelineEntry(
                kind="tool_result",
                tool_call_id="call-a",
                tool_name="read_file",
                tool_result={"output": {"content": "ok"}},
                iteration=1,
            ),
            ChatTimelineEntry(kind="content", content="answer", iteration=2),
        ),
        finished=True,
    )
    assert completed.state is ChatTurnState.COMPLETED
    assert completed.reasoning == "checked"
    assert completed.final_output == "answer"
    assert len(completed.tool_steps) == 1
    assert len(completed.approvals) == 1
    assert [entry.kind for entry in completed.timeline] == [
        "reasoning",
        "tool_call",
        "tool_result",
        "content",
    ]
    assert completed.timeline[2].tool_result["output"] == {"content": "ok"}

    with sqlite3.connect(database) as db:
        names = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "conversation_events" not in names


def test_recovery_fails_interrupted_turn_and_expires_approval(tmp_path: Path) -> None:
    _database, scope, repository = _repository(tmp_path)
    turn, _ = repository.create(
        scope,
        client_request_id="request-a",
        user_input="hello",
    )
    approval, _ = repository.request_approval(
        scope.principal_id,
        turn.turn_id,
        step_id="step-a",
        call=ProposedToolCall(call_id="call-a", name="write_file", arguments={}),
        reason="writes a file",
    )

    recovered = repository.recover_interrupted()

    assert len(recovered) == 1
    assert recovered[0].state is ChatTurnState.FAILED
    assert recovered[0].failure_code == "service_restarted"
    assert recovered[0].approvals[0].approval_id == approval.approval_id
    assert recovered[0].approvals[0].state == "expired"


def test_terminal_checkpoint_expires_racing_approval_and_rejects_late_resolution(
    tmp_path: Path,
) -> None:
    _database, scope, repository = _repository(tmp_path)
    turn, _ = repository.create(
        scope,
        client_request_id="request-a",
        user_input="send a file",
    )
    approval, _ = repository.request_approval(
        scope.principal_id,
        turn.turn_id,
        step_id="step-a",
        call=ProposedToolCall(call_id="call-a", name="attach", arguments={}),
        reason="external_side_effect:high",
    )
    repository.checkpoint(
        scope.principal_id,
        turn.turn_id,
        state=ChatTurnState.COMPLETED,
        final_output="done",
        finished=True,
    )

    resolved, changed, _turn_id = repository.resolve_approval(
        scope.principal_id,
        approval.approval_id,
        approved=True,
        resolved_by="late_channel_callback",
    )

    assert changed is False
    assert resolved.state == "expired"
    stored = repository.get(scope.principal_id, turn.turn_id)
    assert stored.state is ChatTurnState.COMPLETED
    assert stored.approvals[0].resolved_by == "turn_finished"


def test_expired_turn_details_are_compacted_without_deleting_final_history(
    tmp_path: Path,
) -> None:
    now = [1000.0]
    database = tmp_path / "assistant.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(
        database,
        turn_id_factory=lambda: "turn-a",
        clock=lambda: now[0],
        detail_retention_seconds=60,
    )
    turn, _ = repository.create(
        scope,
        client_request_id="request-a",
        user_input="hello",
    )
    call = ProposedToolCall(
        call_id="call-a",
        name="read_file",
        arguments={"path": "/tmp/a"},
    )
    policy = ToolPolicy(
        effect=ToolEffect.READ_ONLY,
        capabilities=frozenset(),
        risk=ToolRisk.LOW,
    )
    step, _ = repository.begin_tool_step(
        scope.principal_id,
        turn.turn_id,
        step_id="step-a",
        call=call,
        policy=policy,
    )
    repository.finish_tool_step(
        scope.principal_id,
        turn.turn_id,
        step.step_id,
        ToolStepResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="completed",
            output={"content": "large internal result"},
        ),
    )
    repository.checkpoint(
        scope.principal_id,
        turn.turn_id,
        state=ChatTurnState.COMPLETED,
        reasoning="working draft",
        content="draft answer",
        final_output="final answer",
        finished=True,
    )

    now[0] += 61
    assert repository.compact_expired_details() == 1
    compacted = repository.get(scope.principal_id, turn.turn_id)
    assert compacted.user_input == "hello"
    assert compacted.final_output == "final answer"
    assert compacted.reasoning == ""
    assert compacted.content == ""
    assert compacted.tool_steps[0].tool_name == "read_file"
    assert compacted.tool_steps[0].result == {}
    assert repository.compact_expired_details() == 0


def test_conversation_sessions_support_history_rename_and_archive(tmp_path: Path) -> None:
    _database, scope, repository = _repository(tmp_path)
    repository.create(
        scope,
        client_request_id="request-a",
        user_input="plan a weekend trip",
    )

    current = repository.get_session(scope.principal_id, scope.session_handle)
    assert current.title == "plan a weekend trip"
    assert current.turn_count == 1

    renamed = repository.update_session(
        scope.principal_id,
        scope.session_handle,
        title="周末计划",
        state=ConversationSessionState.ARCHIVED,
        expected_revision=current.revision,
    )
    assert renamed.title == "周末计划"
    assert renamed.state is ConversationSessionState.ARCHIVED
    with pytest.raises(ConversationSessionConflictError):
        repository.create(
            scope,
            client_request_id="request-b",
            user_input="继续规划",
        )
    assert repository.list_sessions(scope.principal_id) == ((), "")
    assert repository.list_sessions(scope.principal_id, include_archived=True) == ((renamed,), "")


def test_runtime_session_is_not_a_conversation_until_first_turn(tmp_path: Path) -> None:
    _database, scope, repository = _repository(tmp_path)

    assert repository.list_sessions(scope.principal_id) == ((), "")

    turn, created = repository.create(
        scope,
        client_request_id="request-a",
        user_input="  帮我   安排周末行程  ",
    )

    assert created is True
    assert turn.user_input == "  帮我   安排周末行程  "
    sessions, cursor = repository.list_sessions(scope.principal_id)
    assert cursor == ""
    assert len(sessions) == 1
    assert sessions[0].title == "帮我 安排周末行程"
    assert sessions[0].turn_count == 1


def test_conversation_and_turn_history_use_stable_keyset_pagination(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    handles = iter(("session-a", "session-b", "session-c"))
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: next(handles))
    scopes = [sessions.create("principal-a") for _ in range(3)]
    turn_ids = iter(("turn-a", "turn-b", "turn-c", "turn-d", "turn-e"))
    timestamps = iter((100.0, 200.0, 300.0, 400.0, 500.0))
    repository = ConversationRepository(
        database,
        turn_id_factory=lambda: next(turn_ids),
        clock=lambda: next(timestamps),
    )

    for index, scope in enumerate(scopes):
        repository.create(
            scope,
            client_request_id=f"session-request-{index}",
            user_input=f"conversation {index}",
        )

    first_page, session_cursor = repository.list_sessions("principal-a", limit=2)
    second_page, final_session_cursor = repository.list_sessions(
        "principal-a",
        limit=2,
        cursor=session_cursor,
    )
    assert [item.title for item in first_page] == ["conversation 2", "conversation 1"]
    assert [item.title for item in second_page] == ["conversation 0"]
    assert final_session_cursor == ""

    repository.create(
        scopes[0],
        client_request_id="turn-request-d",
        user_input="fourth",
    )
    repository.create(
        scopes[0],
        client_request_id="turn-request-e",
        user_input="fifth",
    )
    newest, turn_cursor = repository.list_session("principal-a", "session-a", limit=2)
    older, final_turn_cursor = repository.list_session(
        "principal-a",
        "session-a",
        limit=2,
        cursor=turn_cursor,
    )
    assert [turn.user_input for turn in newest] == ["fourth", "fifth"]
    assert [turn.user_input for turn in older] == ["conversation 0"]
    assert final_turn_cursor == ""
