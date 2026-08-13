from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository, SessionSnapshot
from knoa_platform.exceptions import SessionNotFoundError


def test_session_handle_is_core_created_and_opaque(tmp_path: Path) -> None:
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "opaque-session-handle",
    )

    scope = repo.create("principal-a")

    assert scope == RuntimeScope(
        principal_id="principal-a",
        session_handle="opaque-session-handle",
    )
    assert "principal-a" not in scope.session_handle
    assert repo.active("principal-a") == scope


def test_session_persists_selected_agent_identity(tmp_path: Path) -> None:
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "codex-session",
    )

    scope = repo.create("principal-a", agent_id="codex")

    assert repo.agent_id(scope) == "codex"
    restarted = RuntimeSessionRepository(tmp_path / "assistant.db")
    assert restarted.agent_id(scope) == "codex"


def test_external_task_scope_is_stable_isolated_and_inherits_agent(tmp_path: Path) -> None:
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "source-session",
    )
    source = repo.create("principal-a", agent_id="codex")

    first = repo.isolated_task_scope(source, "mcp-resource:event-a")
    repeated = repo.isolated_task_scope(source, "mcp-resource:event-a")
    other = repo.isolated_task_scope(source, "mcp-resource:event-b")

    assert first == repeated
    assert first != source
    assert first != other
    assert repo.agent_id(first) == "codex"
    with pytest.raises(SessionNotFoundError):
        repo.isolated_task_scope(
            RuntimeScope(
                principal_id="principal-b",
                session_handle=source.session_handle,
            ),
            "mcp-resource:event-a",
        )


def test_foreign_and_unknown_sessions_are_indistinguishable(tmp_path: Path) -> None:
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "opaque-session-handle",
    )
    scope = repo.create("principal-a")

    with pytest.raises(SessionNotFoundError) as foreign:
        repo.resolve("principal-b", scope.session_handle)
    with pytest.raises(SessionNotFoundError) as unknown:
        repo.resolve("principal-b", "unknown-session")

    assert str(foreign.value) == str(unknown.value) == "Session not found"


def test_transcript_and_context_are_principal_scoped_and_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    repo = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    scope = repo.create("principal-a")
    snapshot = SessionSnapshot(
        messages=(
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ),
    )
    repo.save(scope, snapshot)

    restarted = RuntimeSessionRepository(database)

    assert restarted.load(scope) == snapshot
    with pytest.raises(SessionNotFoundError):
        restarted.load(
            RuntimeScope(
                principal_id="principal-b",
                session_handle=scope.session_handle,
            )
        )


def test_active_session_is_isolated_per_principal(tmp_path: Path) -> None:
    handles = iter(("session-a-1", "session-a-2", "session-b-1"))
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: next(handles),
    )
    first_a = repo.create("principal-a")
    second_a = repo.create("principal-a")
    first_b = repo.create("principal-b")

    assert repo.active("principal-a") == second_a
    assert repo.active("principal-b") == first_b

    repo.set_active(first_a)
    assert repo.active("principal-a") == first_a
    assert repo.active("principal-b") == first_b


def test_delete_cascades_transcript_and_active_pointer(tmp_path: Path) -> None:
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "opaque-session-handle",
    )
    scope = repo.create("principal-a")
    repo.save(scope, SessionSnapshot(messages=({"role": "user", "content": "x"},)))

    repo.delete(scope)

    assert repo.active("principal-a") is None
    assert repo.list_for_principal("principal-a") == ()
    with pytest.raises(SessionNotFoundError):
        repo.load(scope)


def test_foreign_scope_cannot_change_active_or_delete(tmp_path: Path) -> None:
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "opaque-session-handle",
    )
    scope = repo.create("principal-a")
    foreign = RuntimeScope(
        principal_id="principal-b",
        session_handle=scope.session_handle,
    )

    with pytest.raises(SessionNotFoundError):
        repo.set_active(foreign)
    with pytest.raises(SessionNotFoundError):
        repo.delete(foreign)

    assert repo.resolve("principal-a", scope.session_handle) == scope


def test_corrupt_transcript_fails_closed_without_overwrite(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    repo = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "opaque-session-handle",
    )
    scope = repo.create("principal-a")
    repo.save(scope, SessionSnapshot(messages=({"role": "user", "content": "x"},)))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runtime_session_transcripts SET messages_json=? WHERE session_handle=?",
            ("not-json", scope.session_handle),
        )

    with pytest.raises(RuntimeError, match="transcript is corrupt"):
        repo.load(scope)

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT messages_json FROM runtime_session_transcripts WHERE session_handle=?",
            (scope.session_handle,),
        ).fetchone()[0]
    assert stored == "not-json"


def test_incompatible_session_schema_requires_offline_migration(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE runtime_sessions (
                   session_handle TEXT PRIMARY KEY,
                   principal_id TEXT NOT NULL,
                   created_at REAL NOT NULL,
                   updated_at REAL NOT NULL,
                   legacy_state TEXT
               )"""
        )

    with pytest.raises(RuntimeError, match="explicit offline migration"):
        RuntimeSessionRepository(database)


def test_orphan_session_data_fails_foreign_key_check_on_startup(tmp_path: Path) -> None:
    database = tmp_path / "assistant.db"
    RuntimeSessionRepository(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO runtime_session_transcripts(
                   session_handle, messages_json, updated_at
               ) VALUES ('orphan', '[]', 0)"""
        )

    with pytest.raises(RuntimeError, match="foreign-key integrity"):
        RuntimeSessionRepository(database)


def test_transcript_size_is_bounded_before_save(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "knoa_platform.agent_runtime.session_store._MAX_TRANSCRIPT_BYTES",
        32,
    )
    repo = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: "session-a",
    )
    scope = repo.create("principal-a")

    with pytest.raises(ValueError, match="32 byte limit"):
        repo.save(
            scope,
            SessionSnapshot(messages=({"role": "user", "content": "x" * 100},)),
        )

    assert repo.load(scope) == SessionSnapshot()


def test_oversized_stored_transcript_is_rejected_before_json_parse(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.db"
    repo = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = repo.create("principal-a")
    repo.save(scope, SessionSnapshot(messages=({"role": "user", "content": "ok"},)))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runtime_session_transcripts SET messages_json=? WHERE session_handle=?",
            ("x" * 100, scope.session_handle),
        )
    monkeypatch.setattr(
        "knoa_platform.agent_runtime.session_store._MAX_TRANSCRIPT_BYTES",
        32,
    )

    with pytest.raises(RuntimeError, match="storage limit"):
        repo.load(scope)
