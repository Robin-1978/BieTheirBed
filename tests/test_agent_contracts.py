from __future__ import annotations

import asyncio
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
    from pc_assistant.agent_runtime.contracts import (
        AgentRuntimePort,
        ControlServicePort,
        TurnInvoker,
    )

    run_parameters = list(inspect.signature(AgentRuntimePort.run).parameters)
    assert run_parameters[:2] == ["self", "context"]
    assert "session_id" not in run_parameters
    cancel_parameters = list(inspect.signature(AgentRuntimePort.cancel).parameters)
    assert cancel_parameters[:2] == ["self", "scope"]
    assert "session_id" not in cancel_parameters

    for operation in (
        "get_status",
        "get_history",
        "list_memory",
        "clear_memory",
        "list_tools",
        "set_config",
    ):
        parameters = list(inspect.signature(getattr(ControlServicePort, operation)).parameters)
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
        RuntimeRunContext,
        RuntimeScope,
    )

    class Runtime:
        def run(
            self,
            context: RuntimeRunContext,
            request: RunRequest,
        ) -> AsyncIterator[RuntimeEvent]:
            async def stream() -> AsyncIterator[RuntimeEvent]:
                yield RuntimeEvent(
                    event_type="content_delta",
                    payload=RuntimeEventPayload(content=request.input),
                )

            return stream()

    runtime = Runtime()
    scope = RuntimeScope(principal_id="p", session_handle="s")
    request = RunRequest(client_request_id="request-a", input="hello")

    context = RuntimeRunContext(
        scope=scope,
        run_id="run-a",
        cancellation=asyncio.Event(),
    )
    events = [event async for event in runtime.run(context, request)]

    assert [event.payload.content for event in events] == ["hello"]
    assert not inspect.iscoroutinefunction(runtime.run)


# covers agent-contracts-factory#REQ-001
def test_scalar_port_operations_are_awaited_once() -> None:
    from pc_assistant.agent_runtime.contracts import (
        AgentRuntimePort,
        ControlServicePort,
        RuntimeEvent,
    )

    for operation in ("cancel", "health_check"):
        assert inspect.iscoroutinefunction(getattr(AgentRuntimePort, operation))
    for operation in (
        "create_session",
        "get_status",
        "get_history",
        "list_memory",
        "clear_memory",
        "list_tools",
        "set_config",
    ):
        assert inspect.iscoroutinefunction(getattr(ControlServicePort, operation))
    assert not inspect.iscoroutinefunction(AgentRuntimePort.run)

    hints = get_type_hints(AgentRuntimePort.run)
    assert get_origin(hints["return"]) is AsyncIterator
    assert get_args(hints["return"]) == (RuntimeEvent,)


def test_cancel_contract_targets_run_identity() -> None:
    from pc_assistant.agent_runtime.contracts import CancelRequest

    request = CancelRequest(run_id="run-1", reason="user requested")

    assert request.run_id == "run-1"
    with pytest.raises(ValidationError):
        CancelRequest(run_id="")


# covers agent-contracts-factory#REQ-002-S02
def test_internal_runtime_event_is_transport_neutral() -> None:
    from pc_assistant.agent_runtime.contracts import RuntimeEvent, RuntimeEventPayload

    event = RuntimeEvent(
        event_type="content_delta",
        payload=RuntimeEventPayload(content="hello"),
    )

    assert "run_id" not in type(event).model_fields
    assert "event_seq" not in type(event).model_fields
