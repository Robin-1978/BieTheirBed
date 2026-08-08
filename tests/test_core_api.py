from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pc_assistant.agent_runtime.contracts import RuntimeEventPayload
from pc_assistant.agent_runtime.events import RunEventSequencer
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.service.core_api import (
    ConfirmationRequestedMessage,
    CreateSessionRequest,
    DownloadArtifactRequest,
    ResolveConfirmationRequest,
    StartRunRequest,
    core_request_schema,
    parse_core_request_json,
    parse_core_server_message_json,
)


def test_core_request_rejects_legacy_free_form_protocol() -> None:
    legacy = json.dumps({"method": "run", "id": 1, "params": {"input": "hello"}})

    with pytest.raises(ValidationError):
        parse_core_request_json(legacy)


def test_core_request_rejects_caller_supplied_principal_and_unknown_fields() -> None:
    raw = json.dumps(
        {
            "api_version": "v1",
            "request_id": "request-1",
            "method": "run",
            "session_handle": "opaque-session",
            "input": "hello",
            "principal_id": "attacker-selected",
        }
    )

    with pytest.raises(ValidationError):
        parse_core_request_json(raw)


def test_core_request_is_versioned_and_method_specific() -> None:
    request = parse_core_request_json(
        json.dumps(
            {
                "api_version": "v1",
                "request_id": "request-1",
                "method": "run",
                "session_handle": "opaque-session",
                "input": "hello",
            }
        )
    )

    assert isinstance(request, StartRunRequest)
    assert request.input == "hello"
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
        json.dumps(
            {
                "api_version": "v1",
                "request_id": "request-1",
                "method": "create_session",
            }
        )
    )

    assert isinstance(request, CreateSessionRequest)
    with pytest.raises(ValidationError):
        CreateSessionRequest(request_id="request-2", session_handle="chosen")


def test_run_requires_content_or_artifact_reference() -> None:
    with pytest.raises(ValidationError):
        StartRunRequest(
            request_id="request-1",
            session_handle="opaque-session",
        )

    request = StartRunRequest(
        request_id="request-2",
        session_handle="opaque-session",
        attachments=({"artifact_id": "artifact-1"},),
    )
    assert request.attachments[0].artifact_id == "artifact-1"


def test_core_schema_exposes_discriminated_methods() -> None:
    schema = core_request_schema()
    rendered = json.dumps(schema)

    assert "create_session" in rendered
    assert "cancel_run" in rendered
    assert "memory_clear" in rendered
    assert "artifact_upload" in rendered
    assert "artifact_download" in rendered
    assert "confirmation_resolve" in rendered

    request = parse_core_request_json(
        DownloadArtifactRequest(
            request_id="download-1",
            session_handle="session-1",
            artifact_id="artifact-1",
        ).model_dump_json()
    )
    assert isinstance(request, DownloadArtifactRequest)


def test_confirmation_contract_is_strict_and_connection_scoped() -> None:
    request = parse_core_request_json(
        ResolveConfirmationRequest(
            request_id="resolve-1",
            confirmation_id="confirmation-1",
            approved=True,
        ).model_dump_json()
    )
    message = parse_core_server_message_json(
        ConfirmationRequestedMessage(
            request_id="confirmation-confirmation-1",
            confirmation_id="confirmation-1",
            run_id="run-1",
            session_handle="session-a",
            tool_call_id="call-1",
            tool_name="mouse",
            arguments={"action": "click", "x": 10, "y": 20},
            reason="desktop_control:high",
        ).model_dump_json()
    )

    assert isinstance(request, ResolveConfirmationRequest)
    assert request.approved
    assert isinstance(message, ConfirmationRequestedMessage)
    assert message.run_id == "run-1"
    assert message.tool_call_id == "call-1"
    assert message.session_handle == "session-a"


def test_run_events_are_strictly_ordered_and_have_one_terminal_event() -> None:
    sequencer = RunEventSequencer("run-1")

    started = sequencer.emit("run_started")
    delta = sequencer.emit(
        "content_delta",
        RuntimeEventPayload(content="hello"),
    )
    final_output = sequencer.emit(
        "final_output",
        RuntimeEventPayload(content="hello"),
    )
    completed = sequencer.emit(
        "completed",
        RuntimeEventPayload(content="hello"),
    )

    assert [
        started.event_seq,
        delta.event_seq,
        final_output.event_seq,
        completed.event_seq,
    ] == [1, 2, 3, 4]
    assert completed.is_terminal
    assert sequencer.terminal
    with pytest.raises(RuntimeError, match="terminal"):
        sequencer.emit("failed", RuntimeEventPayload(content="late failure"))


def test_run_event_rejects_unknown_public_event_type() -> None:
    sequencer = RunEventSequencer("run-1")

    with pytest.raises(ValidationError):
        sequencer.emit("legacy_stream_delta")  # type: ignore[arg-type]


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
