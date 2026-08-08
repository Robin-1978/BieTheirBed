from __future__ import annotations

import asyncio

import pytest

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.agent_runtime.run_registry import (
    CoreRunRegistry,
    RunCapacityExceededError,
    RunHandle,
)


def _scope(principal: str = "principal-a", session: str = "session-a") -> RuntimeScope:
    return RuntimeScope(principal_id=principal, session_handle=session)


def test_core_generates_run_identity_and_tracks_scope() -> None:
    registry = CoreRunRegistry(run_id_factory=lambda: "run-opaque")

    handle = registry.start(_scope())

    assert handle.run_id == "run-opaque"
    assert handle.scope == _scope()
    assert registry.status("principal-a", "run-opaque") == "running"


def test_cancel_is_principal_scoped_and_foreign_matches_unknown() -> None:
    registry = CoreRunRegistry(run_id_factory=lambda: "run-opaque")
    handle = registry.start(_scope())

    foreign = registry.request_cancel("principal-b", handle.run_id)
    unknown = registry.request_cancel("principal-b", "missing-run")

    assert foreign == unknown
    assert foreign.accepted is False
    assert foreign.status == "not_found"
    assert not handle.cancel_requested


def test_cancel_sets_request_local_async_event() -> None:
    registry = CoreRunRegistry(run_id_factory=lambda: "run-opaque")
    handle = registry.start(_scope())

    result = registry.request_cancel("principal-a", handle.run_id)

    assert result.accepted
    assert result.status == "cancelling"
    assert handle.cancel_requested
    assert registry.status("principal-a", handle.run_id) == "cancelling"


def test_terminal_transition_is_idempotent_but_cannot_change_outcome() -> None:
    registry = CoreRunRegistry(run_id_factory=lambda: "run-opaque")
    handle = registry.start(_scope())

    assert registry.finish(handle, "completed") == "completed"
    assert registry.finish(handle, "completed") == "completed"
    with pytest.raises(RuntimeError, match="already terminated"):
        registry.finish(handle, "failed")

    result = registry.request_cancel("principal-a", handle.run_id)
    assert result.accepted
    assert result.status == "completed"


def test_registry_rejects_forged_run_handle() -> None:
    registry = CoreRunRegistry(run_id_factory=lambda: "run-opaque")
    issued = registry.start(_scope())
    forged = RunHandle(
        run_id=issued.run_id,
        scope=issued.scope,
        cancellation=asyncio.Event(),
        _capability=object(),
    )

    with pytest.raises(PermissionError):
        registry.finish(forged, "failed")


def test_active_run_cannot_be_forgotten_and_terminal_run_can() -> None:
    registry = CoreRunRegistry(run_id_factory=lambda: "run-opaque")
    handle = registry.start(_scope())

    with pytest.raises(RuntimeError, match="Active run"):
        registry.forget(handle)

    registry.finish(handle, "cancelled")
    registry.forget(handle)

    assert registry.status("principal-a", handle.run_id) is None


def test_run_id_collision_retries_without_overwriting_active_run() -> None:
    ids = iter(("same-run", "same-run", "second-run"))
    registry = CoreRunRegistry(run_id_factory=lambda: next(ids))

    first = registry.start(_scope(session="session-a"))
    second = registry.start(_scope(session="session-b"))

    assert first.run_id == "same-run"
    assert second.run_id == "second-run"
    assert registry.status("principal-a", first.run_id) == "running"
    assert registry.status("principal-a", second.run_id) == "running"


def test_global_run_capacity_is_bounded_and_reusable() -> None:
    ids = iter(("run-a", "run-b", "run-c"))
    registry = CoreRunRegistry(
        run_id_factory=lambda: next(ids),
        max_active_runs=1,
    )
    first = registry.start(_scope())

    with pytest.raises(RunCapacityExceededError, match="Global active run limit"):
        registry.start(_scope())

    registry.finish(first, "completed")
    registry.forget(first)
    assert registry.start(_scope()).run_id == "run-c"


def test_global_run_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CoreRunRegistry(max_active_runs=0)
