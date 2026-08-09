from __future__ import annotations

import sqlite3

import pytest

from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.gateway.auth import GatewayAuthRepository
from pc_assistant.gateway.identity import GatewayIdentityRepository
from pc_assistant.principal import converge_owner_principals
from pc_assistant.runtime import RuntimePaths


def _memory(
    connection: sqlite3.Connection,
    principal: str,
    key: str,
    value: str,
    updated_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO memories (
            principal_id, key, value, category, importance, confidence,
            source, created_at, updated_at, last_used_at, access_count
        ) VALUES (?, ?, ?, 'preference', 'core', 1.0, 'user', ?, ?, NULL, 0)
        """,
        (principal, key, value, updated_at, updated_at),
    )


def test_owner_principal_convergence_preserves_newest_memory_and_sessions(
    tmp_path,
) -> None:
    paths = RuntimePaths.from_root(tmp_path)
    database = paths.data / "assistant.db"
    RuntimeSessionRepository(
        database,
        handle_factory=iter(("local-session", "feishu-session")).__next__,
    ).create("local")
    sessions = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "feishu-session",
    )
    sessions.create("personal:feishu:abc")
    SQLiteMemoryRepository(database)
    with sqlite3.connect(database) as connection:
        _memory(connection, "local", "user_name", "old", "2026-01-01T00:00:00Z")
        _memory(
            connection,
            "personal:feishu:abc",
            "user_name",
            "Robin",
            "2026-08-09T00:00:00Z",
        )

    converge_owner_principals(
        paths,
        "personal:owner",
        ("local", "personal:feishu:abc"),
    )
    converge_owner_principals(
        paths,
        "personal:owner",
        ("local", "personal:feishu:abc"),
    )

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM memories WHERE principal_id=? AND key=?",
            ("personal:owner", "user_name"),
        ).fetchone() == ("Robin",)
        assert connection.execute(
            "SELECT count(*) FROM runtime_sessions WHERE principal_id=?",
            ("personal:owner",),
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM memories WHERE principal_id IN (?, ?)",
            ("local", "personal:feishu:abc"),
        ).fetchone() == (0,)


def test_owner_principal_convergence_updates_gateway_identity_state(tmp_path) -> None:
    paths = RuntimePaths.from_root(tmp_path)
    database = paths.data / "gateway.db"
    identities = GatewayIdentityRepository(database)
    GatewayAuthRepository(database)
    identities.create_pairing_grant("local", ttl_seconds=60)

    converge_owner_principals(paths, "personal:owner", ("local",))

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT DISTINCT principal_id FROM gateway_pairing_grants"
        ).fetchall() == [("personal:owner",)]


def test_owner_principal_convergence_rolls_back_on_unique_conflict(tmp_path) -> None:
    paths = RuntimePaths.from_root(tmp_path)
    database = paths.data / "assistant.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE conflicting (
                principal_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                UNIQUE(principal_id, external_id)
            );
            INSERT INTO conflicting VALUES ('local', 'same');
            INSERT INTO conflicting VALUES ('personal:owner', 'same');
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        converge_owner_principals(paths, "personal:owner", ("local",))

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT principal_id FROM conflicting ORDER BY principal_id"
        ).fetchall() == [("local",), ("personal:owner",)]
