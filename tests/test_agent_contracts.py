from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError


# covers agent-contracts-factory#REQ-001-S01
# covers agent-contracts-factory#REQ-001-S02
def test_runtime_scope_requires_non_empty_principal_and_session() -> None:
    from pc_assistant.agent_runtime.contracts import RuntimeScope

    scope = RuntimeScope(principal_id="principal-1", session_handle="session-1")

    assert scope.principal_id == "principal-1"
    assert scope.session_handle == "session-1"
    assert "principal-1" not in scope.model_dump_json(exclude={"principal_id"})
    with pytest.raises(ValidationError):
        RuntimeScope(principal_id="", session_handle="session-1")
    with pytest.raises(ValidationError):
        RuntimeScope(principal_id="principal-1", session_handle=" ")


# covers agent-contracts-factory#REQ-001-S03
# covers agent-contracts-factory#REQ-001-S04
# covers agent-contracts-factory#REQ-001-S05
# covers agent-contracts-factory#REQ-001-S06
def test_session_operations_have_no_bare_session_overload() -> None:
    from pc_assistant.agent_runtime.contracts import AgentRuntimePort, TurnInvoker

    for operation in ("run", "cancel", "get_status", "command"):
        parameters = list(inspect.signature(getattr(AgentRuntimePort, operation)).parameters)
        assert parameters[:2] == ["self", "scope"]
        assert "session_id" not in parameters

    invoker_parameters = list(inspect.signature(TurnInvoker.__call__).parameters)
    assert invoker_parameters[:2] == ["self", "scope"]
    assert "session_id" not in invoker_parameters


# covers agent-contracts-factory#REQ-001-S07
def test_health_check_is_unscoped_async_scalar() -> None:
    from pc_assistant.agent_runtime.contracts import AgentRuntimePort

    assert list(inspect.signature(AgentRuntimePort.health_check).parameters) == ["self"]
    assert inspect.iscoroutinefunction(AgentRuntimePort.health_check)


# covers agent-contracts-factory#REQ-001-S08
@pytest.mark.asyncio
async def test_run_and_turn_invoker_are_direct_async_iterators() -> None:
    from pc_assistant.agent_runtime.contracts import (
        RunRequest,
        RuntimeEvent,
        RuntimeEventPayload,
        RuntimeScope,
    )

    class Runtime:
        def run(
            self,
            scope: RuntimeScope,
            request: RunRequest,
        ) -> AsyncIterator[RuntimeEvent]:
            async def stream() -> AsyncIterator[RuntimeEvent]:
                yield RuntimeEvent(
                    event_type="final_answer",
                    payload=RuntimeEventPayload(content=request.input),
                )

            return stream()

    runtime = Runtime()
    scope = RuntimeScope(principal_id="p", session_handle="s")
    request = RunRequest(input="hello")

    events = [event async for event in runtime.run(scope, request)]

    assert [event.payload.content for event in events] == ["hello"]
    assert not inspect.iscoroutinefunction(runtime.run)


# covers agent-contracts-factory#REQ-001
def test_scalar_port_operations_are_awaited_once() -> None:
    from pc_assistant.agent_runtime.contracts import AgentRuntimePort, RuntimeEvent

    for operation in ("cancel", "health_check", "get_status", "command"):
        assert inspect.iscoroutinefunction(getattr(AgentRuntimePort, operation))
    assert not inspect.iscoroutinefunction(AgentRuntimePort.run)

    hints = get_type_hints(AgentRuntimePort.run)
    assert get_origin(hints["return"]) is AsyncIterator
    assert get_args(hints["return"]) == (RuntimeEvent,)


# covers agent-contracts-factory#REQ-002-S01
def test_public_run_event_requires_versioned_identity_sequence_and_typed_payload() -> None:
    from pc_assistant.agent_runtime.contracts import RunEvent, RuntimeEventPayload

    event = RunEvent(
        run_id="run-1",
        event_seq=1,
        event_type="stream_delta",
        payload=RuntimeEventPayload(content="hello"),
    )

    assert event.api_version == "v1"
    assert event.payload.content == "hello"
    with pytest.raises(ValidationError):
        RunEvent(
            run_id="run-1",
            event_seq=0,
            event_type="stream_delta",
            payload=RuntimeEventPayload(content="hello"),
        )


# covers agent-contracts-factory#REQ-002-S02
def test_internal_runtime_event_is_transport_neutral() -> None:
    from pc_assistant.agent_runtime.contracts import RuntimeEvent, RuntimeEventPayload

    event = RuntimeEvent(
        event_type="stream_delta",
        payload=RuntimeEventPayload(content="hello"),
    )

    assert "run_id" not in type(event).model_fields
    assert "event_seq" not in type(event).model_fields


# covers agent-contracts-factory#REQ-005-S01
def test_runtime_package_exports_only_canonical_contract_names() -> None:
    import pc_assistant.agent_runtime as runtime_package

    expected = {
        "AgentFactory",
        "AgentRuntimePort",
        "CancelRequest",
        "CancelResult",
        "CommandRequest",
        "CommandResult",
        "HealthStatus",
        "RunEvent",
        "RunRequest",
        "RuntimeEvent",
        "RuntimeEventPayload",
        "RuntimeScope",
        "RuntimeStatus",
        "StatusRequest",
        "TurnInvoker",
    }

    assert expected <= set(runtime_package.__all__)


# covers agent-contracts-factory#REQ-005-S02
@pytest.mark.parametrize(
    "rejected_name",
    [
        "AgentLike",
        "ServiceClient",
        "AgentEvent",
        "RuntimeControl",
        "TurnOrchestrator",
        "Agent",
    ],
)
def test_runtime_package_rejects_legacy_aliases(rejected_name: str) -> None:
    import pc_assistant.agent_runtime as runtime_package

    assert rejected_name not in runtime_package.__all__
    assert not hasattr(runtime_package, rejected_name)
