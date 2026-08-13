from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from knoa_platform.artifacts import ArtifactRef
from knoa_platform.agent_runtime.contracts import ArtifactTranscriptionResult
from knoa_platform.automation import ScheduleKind, ScheduleSpec
from knoa_platform.service.core_api import (
    CreateSessionRequest,
    CreateScheduleRequest,
    CreateTriggerRequest,
    CreateTaskRequest,
    DownloadArtifactRequest,
    FireTriggerRequest,
    GetTaskRequest,
    ListTasksRequest,
    PauseScheduleRequest,
    PauseTaskRequest,
    ResolveApprovalRequest,
    ResumeTaskRequest,
    ResumeScheduleRequest,
    SubscribePrincipalTaskEventsRequest,
    SubscribeTaskRequest,
    UnsubscribeRequest,
    PrincipalTaskEventMessage,
    TaskEventMessage,
    TranscribeArtifactRequest,
    ArtifactTranscribedMessage,
    core_request_schema,
    parse_core_request_json,
    parse_core_server_message_json,
)
from knoa_platform.tasks import (
    PrincipalTaskEvent,
    TaskEvent,
    TaskEventPayload,
    TaskState,
)


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


def test_unsubscribe_targets_an_existing_subscription_request() -> None:
    request = parse_core_request_json(
        UnsubscribeRequest(
            request_id="unsubscribe-1",
            subscription_request_id="principal-feed-1",
        ).model_dump_json()
    )

    assert isinstance(request, UnsubscribeRequest)
    assert request.subscription_request_id == "principal-feed-1"


def test_principal_task_event_feed_is_strict_and_versioned() -> None:
    request = parse_core_request_json(
        SubscribePrincipalTaskEventsRequest(
            request_id="principal-feed-1",
            after_id=11,
        ).model_dump_json()
    )
    message = parse_core_server_message_json(
        PrincipalTaskEventMessage(
            request_id="principal-feed-1",
            feed_event=PrincipalTaskEvent(
                feed_event_id=12,
                principal_id="principal-a",
                event=TaskEvent(
                    task_id="task-a",
                    event_seq=3,
                    event_type="completed",
                    payload=TaskEventPayload(state=TaskState.COMPLETED),
                    occurred_at=1.0,
                ),
            ),
        ).model_dump_json()
    )

    assert isinstance(request, SubscribePrincipalTaskEventsRequest)
    assert request.after_id == 11
    assert isinstance(message, PrincipalTaskEventMessage)
    assert message.feed_event.feed_event_id == 12
    with pytest.raises(ValidationError):
        parse_core_request_json(
            json.dumps(
                {
                    "api_version": "v1",
                    "request_id": "principal-feed-2",
                    "method": "subscribe_principal_task_events",
                    "after_id": 0,
                    "principal_id": "attacker-selected",
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
    assert "subscribe_principal_task_events" in rendered
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
    assert "create_trigger" in rendered
    assert "get_trigger" in rendered
    assert "list_triggers" in rendered
    assert "pause_trigger" in rendered
    assert "resume_trigger" in rendered
    assert "fire_trigger" in rendered
    assert "artifact_transcribe" in rendered
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

    transcription_request = parse_core_request_json(
        TranscribeArtifactRequest(
            request_id="transcribe-1",
            session_handle="session-1",
            artifact_id="artifact-1",
        ).model_dump_json()
    )
    transcription_message = parse_core_server_message_json(
        ArtifactTranscribedMessage(
            request_id="transcribe-1",
            result=ArtifactTranscriptionResult(
                artifact_id="artifact-1",
                transcript="hello",
                tool_name="mcp__speech__transcribe",
            ),
        ).model_dump_json()
    )
    assert isinstance(transcription_request, TranscribeArtifactRequest)
    assert isinstance(transcription_message, ArtifactTranscribedMessage)

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

    trigger = parse_core_request_json(
        CreateTriggerRequest(
            request_id="trigger-1",
            session_handle="session-1",
            name="gitlab merge",
            goal="review merge request",
        ).model_dump_json()
    )
    event = parse_core_request_json(
        FireTriggerRequest(
            request_id="event-1",
            trigger_id="trigger-1",
            external_event_id="gitlab-event-1",
            payload={"project": "knoa"},
        ).model_dump_json()
    )
    assert isinstance(trigger, CreateTriggerRequest)
    assert isinstance(event, FireTriggerRequest)


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
