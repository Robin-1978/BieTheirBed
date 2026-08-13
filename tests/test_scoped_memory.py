from __future__ import annotations

import sqlite3

import pytest

from knoa_platform.context.memory_db import (
    SQLiteMemoryRepository,
    ScopedEpisodicMemory,
    ScopedUserMemory,
    validate_memory_key,
)
from knoa_platform.context.scope import (
    MemoryScope,
    current_memory_scope,
    reset_memory_scope,
    set_memory_scope,
)


class scoped_as:
    def __init__(self, principal_id: str, session_id: str) -> None:
        self.scope = MemoryScope(principal_id, session_id)

    def __enter__(self):
        self.token = set_memory_scope(self.scope)
        return self.scope

    def __exit__(self, *_args):
        reset_memory_scope(self.token)


@pytest.fixture
def stores(tmp_path):
    repository = SQLiteMemoryRepository(tmp_path / "context.db")
    return ScopedUserMemory(repository), ScopedEpisodicMemory(repository)


def test_memory_access_without_request_scope_fails_closed(stores):
    memory, _ = stores

    with pytest.raises(RuntimeError, match="scope is not bound"):
        current_memory_scope()
    with pytest.raises(RuntimeError, match="scope is not bound"):
        memory.retrieve("preferred_language")


def test_explicit_identity_keys_remain_distinct(stores):
    memory, _ = stores
    with scoped_as("local", "tui:one"):
        memory.store("user_name", "Robin", category="identity", importance="core")
        memory.store("assistant_name", "Knoa", category="identity", importance="core")

        assert memory.retrieve("user_name").value == "Robin"
        assert memory.retrieve("assistant_name").value == "Knoa"


def test_ambiguous_name_key_is_rejected():
    with pytest.raises(ValueError, match="user_name or assistant_name"):
        validate_memory_key("name")


def test_principal_isolation_and_same_principal_sharing(stores):
    memory, _ = stores
    with scoped_as("user:a", "tui:first"):
        memory.store("preferred_language", "Chinese", category="communication", importance="core")

    with scoped_as("user:a", "tui:second"):
        assert memory.retrieve("preferred_language").value == "Chinese"

    with scoped_as("user:b", "tui:first"):
        assert memory.retrieve("preferred_language") is None


def test_core_is_always_injected_but_relevant_requires_match(stores):
    memory, _ = stores
    with scoped_as("local", "tui:one"):
        memory.store("user_name", "Robin", category="identity", importance="core")
        memory.store("preferred_browser", "Firefox", category="preference", importance="relevant")

        unrelated = memory.build_context_string(query="今天天气如何")
        assert "user_name: Robin" in unrelated
        assert "preferred_browser" not in unrelated

        related = memory.build_context_string(query="请用 Firefox 打开网页")
        assert "user_name: Robin" in related
        assert "preferred_browser: Firefox" in related


def test_episode_isolated_by_both_principal_and_session(stores):
    _, episodes = stores
    with scoped_as("user:a", "session:one"):
        episodes.store_episode("fixed the clipboard bug")
        assert len(episodes.recall()) == 1

    with scoped_as("user:a", "session:two"):
        assert episodes.recall() == []

    with scoped_as("user:b", "session:one"):
        assert episodes.recall() == []


def test_incompatible_memory_schema_requires_offline_migration(tmp_path) -> None:
    database = tmp_path / "context.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE memories (principal_id TEXT, key TEXT)"
        )

    with pytest.raises(RuntimeError, match="explicit offline migration"):
        SQLiteMemoryRepository(database)
