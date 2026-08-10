from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import ArtifactAttachment
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.agent_runtime.tool_step import ProposedToolCall, ToolStepResult
from pc_assistant.conversation import (
    ChatTurnConflictError,
    ChatTurnState,
    ConversationRepository,
    ConversationSessionState,
)
from pc_assistant.tools.base import ToolEffect, ToolPolicy, ToolRisk


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
    assert repository.list_session("principal-a", "session-a") == (created,)

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
        finished=True,
    )
    assert completed.state is ChatTurnState.COMPLETED
    assert completed.reasoning == "checked"
    assert completed.final_output == "answer"
    assert len(completed.tool_steps) == 1
    assert len(completed.approvals) == 1

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
    session = repository.create_session(scope)
    repository.create(
        scope,
        client_request_id="request-a",
        user_input="plan a weekend trip",
    )

    current = repository.get_session(scope.principal_id, scope.session_handle)
    assert current.title == "新对话"
    repository.touch_session(
        scope.principal_id,
        scope.session_handle,
        first_input="plan a weekend trip",
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
    assert repository.list_sessions(scope.principal_id) == ()
    assert repository.list_sessions(scope.principal_id, include_archived=True) == (renamed,)
