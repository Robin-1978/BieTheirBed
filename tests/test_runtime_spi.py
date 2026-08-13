from __future__ import annotations

import inspect

import pytest
from pydantic import TypeAdapter, ValidationError

from knoa_agent_contracts import (
    AgentRuntime,
    ArtifactPart,
    ArtifactReference,
    AssistantDelta,
    McpEndpointGrant,
    RuntimeSession,
    RuntimeTurnEvent,
    RuntimeTurnRequest,
    TextPart,
    TurnFinished,
)


def test_agent_runtime_spi_has_persistent_session_and_turn_lifecycle() -> None:
    assert list(inspect.signature(AgentRuntime.create_session).parameters) == [
        "self",
        "request",
    ]
    assert list(inspect.signature(AgentRuntime.start_turn).parameters) == [
        "self",
        "request",
    ]
    assert inspect.iscoroutinefunction(AgentRuntime.create_session)
    assert inspect.iscoroutinefunction(AgentRuntime.start_turn)
    assert inspect.iscoroutinefunction(AgentRuntime.reconcile)
    assert inspect.iscoroutinefunction(AgentRuntime.delete_session)


def test_runtime_request_is_wire_safe_and_epoch_fenced() -> None:
    session = RuntimeSession(
        agent_id="knoa",
        runtime_session_ref="agent-session-a",
        runtime_protocol_version="1.0",
        binding_epoch=2,
    )
    grant = McpEndpointGrant(
        server_id="platform",
        transport="in_memory",
        endpoint="memory://platform-capabilities",
        authorization="secret-token",
        expires_at=10.0,
        scope_digest="a" * 64,
        binding_epoch=2,
    )
    request = RuntimeTurnRequest(
        session=session,
        operation_id="operation-a",
        input=(TextPart(text="hello"),),
        mcp=grant,
    )

    encoded = request.model_dump_json()
    assert "callback" not in encoded
    assert "repository" not in encoded
    assert "path" not in encoded
    assert "secret-token" not in repr(grant)

    with pytest.raises(ValidationError, match="binding epochs differ"):
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-b",
            input=(TextPart(text="hello"),),
            mcp=grant.model_copy(update={"binding_epoch": 3}),
        )


def test_artifact_input_is_reference_plus_mcp_resource_not_host_path() -> None:
    part = ArtifactPart(
        artifact=ArtifactReference(
            artifact_id="artifact-a",
            name="screen.png",
            media_type="image/png",
            size_bytes=12,
            sha256="b" * 64,
        ),
        resource_uri="knoa-artifact://artifact-a",
        presentation="image",
    )

    assert "path" not in type(part).model_fields
    assert part.resource_uri == "knoa-artifact://artifact-a"


def test_runtime_events_are_discriminated_and_terminal_is_explicit() -> None:
    adapter = TypeAdapter(RuntimeTurnEvent)
    delta = AssistantDelta(
        runtime_session_ref="session-a",
        runtime_turn_ref="turn-a",
        occurred_at=1.0,
        content="hello",
    )
    terminal = TurnFinished(
        runtime_session_ref="session-a",
        runtime_turn_ref="turn-a",
        occurred_at=2.0,
        status="completed",
        final_output="hello",
    )

    assert type(adapter.validate_python(delta.model_dump())) is AssistantDelta
    assert type(adapter.validate_python(terminal.model_dump())) is TurnFinished
    assert "payload" not in type(delta).model_fields
