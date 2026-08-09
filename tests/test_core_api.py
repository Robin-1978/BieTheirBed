from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pc_assistant.artifacts import ArtifactRef
from pc_assistant.automation import ScheduleKind, ScheduleSpec
from pc_assistant.service.core_api import (
    CreateSessionRequest,
    CreateScheduleRequest,
    CreateTaskRequest,
    DownloadArtifactRequest,
    GetTaskRequest,
    ListTasksRequest,
    PauseScheduleRequest,
    PauseTaskRequest,
    ResolveApprovalRequest,
    ResumeTaskRequest,
    ResumeScheduleRequest,
    SubscribeTaskRequest,
    TaskEventMessage,
    core_request_schema,
    parse_core_request_json,
    parse_core_server_message_json,
)
from pc_assistant.tasks import TaskEvent, TaskEventPayload, TaskState


def test_core_request_rejects_legacy_free_form_protocol() -> None:
    legacy = json.dumps({"method": "run", "id": 1, "params": {"input": "hello"}})

    with pytest.raises(ValidationError):
        parse_core_request_json(legacy)


def test_task_request_rejects_caller_supplied_principal() -> None:
    raw = json.dumps(
        {
            "api_version": "v1",
            "request_id": "request-1",
            "method": "create_task",
            "session_handle": "opaque-session",
            "input": "hello",
            "principal_id": "attacker-selected",
        }
    )

    with pytest.raises(ValidationError):
        parse_core_request_json(raw)


def test_task_commands_are_versioned_and_method_specific() -> None:
    request = parse_core_request_json(
        CreateTaskRequest(
            request_id="request-1",
            session_handle="opaque-session",
            input="hello",
        ).model_dump_json()
    )
    subscription = parse_core_request_json(
        SubscribeTaskRequest(
            request_id="subscribe-1",
            task_id="task-1",
            after_seq=7,
        ).model_dump_json()
    )

    assert isinstance(request, CreateTaskRequest)
    assert isinstance(subscription, SubscribeTaskRequest)
    assert subscription.after_seq == 7
    with pytest.raises(ValidationError):
        parse_core_request_json(
            json.dumps(
                {
                    "api_version": "v2",
                    "request_id": "request-2",
                    "method": "create_session",
                }
            )
        )


def test_create_session_has_no_caller_selected_session_handle() -> None:
    request = parse_core_request_json(
        CreateSessionRequest(request_id="request-1").model_dump_json()
    )

    assert isinstance(request, CreateSessionRequest)
    with pytest.raises(ValidationError):
        CreateSessionRequest(request_id="request-2", session_handle="chosen")


def test_task_requires_content_or_artifact_reference() -> None:
    with pytest.raises(ValidationError):
        CreateTaskRequest(
            request_id="request-1",
            session_handle="opaque-session",
        )

    request = CreateTaskRequest(
        request_id="request-2",
        session_handle="opaque-session",
        attachments=({"artifact_id": "artifact-1"},),
    )
    assert request.attachments[0].artifact_id == "artifact-1"


def test_core_schema_exposes_only_task_lifecycle_methods() -> None:
    rendered = json.dumps(core_request_schema())

    assert "create_task" in rendered
    assert "subscribe_task" in rendered
    assert "get_task" in rendered
    assert "list_tasks" in rendered
    assert "cancel_task" in rendered
    assert "pause_task" in rendered
    assert "resume_task" in rendered
    assert "approval_resolve" in rendered
    assert "create_schedule" in rendered
    assert "get_schedule" in rendered
    assert "list_schedules" in rendered
    assert "pause_schedule" in rendered
    assert "resume_schedule" in rendered
    assert "cancel_run" not in rendered
    assert "confirmation_resolve" not in rendered

    request = parse_core_request_json(
        DownloadArtifactRequest(
            request_id="download-1",
            session_handle="session-1",
            artifact_id="artifact-1",
        ).model_dump_json()
    )
    assert isinstance(request, DownloadArtifactRequest)

    resumed = parse_core_request_json(
        ResumeTaskRequest(
            request_id="resume-1",
            task_id="task-1",
            reason="reviewed recovery state",
            acknowledge_outcome_unknown=True,
        ).model_dump_json()
    )
    assert isinstance(resumed, ResumeTaskRequest)
    assert resumed.acknowledge_outcome_unknown is True

    paused = parse_core_request_json(
        PauseTaskRequest(
            request_id="pause-1",
            task_id="task-1",
            reason="pause from phone",
        ).model_dump_json()
    )
    assert isinstance(paused, PauseTaskRequest)

    detail = parse_core_request_json(
        GetTaskRequest(request_id="detail-1", task_id="task-1").model_dump_json()
    )
    listing = parse_core_request_json(
        ListTasksRequest(
            request_id="list-1",
            state=TaskState.PAUSED,
            limit=25,
        ).model_dump_json()
    )
    assert isinstance(detail, GetTaskRequest)
    assert isinstance(listing, ListTasksRequest)

    schedule = parse_core_request_json(
        CreateScheduleRequest(
            request_id="schedule-1",
            session_handle="session-1",
            goal="prepare report",
            spec=ScheduleSpec(
                kind=ScheduleKind.CRON,
                cron_expression="0 9 * * 1-5",
            ),
        ).model_dump_json()
    )
    assert isinstance(schedule, CreateScheduleRequest)
    assert isinstance(
        parse_core_request_json(
            PauseScheduleRequest(
                request_id="pause-schedule-1",
                schedule_id="schedule-1",
            ).model_dump_json()
        ),
        PauseScheduleRequest,
    )
    assert isinstance(
        parse_core_request_json(
            ResumeScheduleRequest(
                request_id="resume-schedule-1",
                schedule_id="schedule-1",
            ).model_dump_json()
        ),
        ResumeScheduleRequest,
    )


def test_approval_and_task_event_wire_contracts_are_strict() -> None:
    request = parse_core_request_json(
        ResolveApprovalRequest(
            request_id="resolve-1",
            approval_id="approval-1",
            approved=True,
        ).model_dump_json()
    )
    message = parse_core_server_message_json(
        TaskEventMessage(
            request_id="subscription-1",
            event=TaskEvent(
                task_id="task-1",
                event_seq=3,
                event_type="approval_requested",
                payload=TaskEventPayload(
                    state=TaskState.WAITING_APPROVAL,
                    approval_id="approval-1",
                    tool_call_id="call-1",
                    tool_name="mouse",
                    tool_args={"action": "click", "x": 10, "y": 20},
                    reason="desktop_control:high",
                ),
                occurred_at=1.0,
            ),
        ).model_dump_json()
    )

    assert isinstance(request, ResolveApprovalRequest)
    assert isinstance(message, TaskEventMessage)
    assert message.event.payload.approval_id == "approval-1"
    with pytest.raises(ValidationError):
        TaskEvent(
            task_id="task-1",
            event_seq=1,
            event_type="task_created",
            payload=TaskEventPayload(state=TaskState.QUEUED),
        )


def test_public_artifact_reference_is_strict_and_immutable() -> None:
    ref = ArtifactRef(
        artifact_id="artifact-1",
        kind="image",
        name="capture.png",
        media_type="image/png",
        size=12,
    )

    with pytest.raises(ValidationError):
        ArtifactRef(
            artifact_id="artifact-1",
            kind="image",
            name="capture.png",
            media_type="image/png",
            size=12,
            path="/secret/path",
        )
    with pytest.raises(ValidationError):
        ref.size = 13
