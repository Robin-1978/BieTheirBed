from __future__ import annotations

from pathlib import Path

import pytest

from knoa_agent import (
    ContextCheckpoint,
    ContextCheckpointConflictError,
    ContextCheckpointRepository,
)


def test_context_store_owns_persistent_agent_session_and_cas_checkpoint(
    tmp_path: Path,
) -> None:
    times = iter((1.0, 2.0, 3.0))
    repository = ContextCheckpointRepository(
        tmp_path / "knoa-agent" / "context.db",
        session_id_factory=lambda: "agent-session-a",
        clock=lambda: next(times),
    )
    session = repository.create_session(operation_id="create-a", state_version="1")
    assert repository.create_session(
        operation_id="create-a",
        state_version="1",
    ) == session
    initial = ContextCheckpoint(
        runtime_session_ref=session.runtime_session_ref,
        state_version="1",
        source_cursor=2,
        agent_config_digest="config-a",
        model_context_digest="context-a",
        payload={"summary": "bounded summary"},
        revision=1,
        created_at=0.0,
        updated_at=0.0,
    )

    saved = repository.save_checkpoint(initial, expected_revision=None)
    updated = repository.save_checkpoint(
        saved.model_copy(update={"source_cursor": 3}),
        expected_revision=saved.revision,
    )

    assert session.runtime_session_ref == "agent-session-a"
    assert repository.load_checkpoint(session.runtime_session_ref) == updated
    with pytest.raises(ContextCheckpointConflictError):
        repository.save_checkpoint(initial, expected_revision=1)


def test_deleting_agent_session_cascades_private_checkpoint(tmp_path: Path) -> None:
    repository = ContextCheckpointRepository(
        tmp_path / "context.db",
        session_id_factory=lambda: "agent-session-a",
    )
    session = repository.create_session(operation_id="create-a", state_version="1")
    repository.save_checkpoint(
        ContextCheckpoint(
            runtime_session_ref=session.runtime_session_ref,
            state_version="1",
            source_cursor=0,
            agent_config_digest="config-a",
            model_context_digest="context-a",
            payload={},
            revision=1,
            created_at=0.0,
            updated_at=0.0,
        ),
        expected_revision=None,
    )

    repository.delete_session(session.runtime_session_ref)

    with pytest.raises(LookupError):
        repository.get_session(session.runtime_session_ref)
    with pytest.raises(LookupError):
        repository.load_checkpoint(session.runtime_session_ref)
