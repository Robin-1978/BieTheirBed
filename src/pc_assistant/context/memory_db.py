"""Principal-scoped SQLite memory repository and runtime bindings."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pc_assistant.context.scope import current_memory_scope
from pc_assistant.sqlite_connection import connect_sqlite, initialize_wal
from pc_assistant.sqlite_schema import require_exact_table, require_index_columns

AMBIGUOUS_MEMORY_KEYS = frozenset({
    "name", "language", "style", "preference", "location", "browser",
    "editor", "framework", "assistant", "user",
})
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_IMPORTANCE = frozenset({"core", "relevant"})
_CORE_CATEGORIES = frozenset({"identity", "communication", "safety"})
_MEMORY_CATEGORIES = frozenset({
    "general", "identity", "communication", "preference", "workflow",
    "safety", "environment", "instruction",
})
_SENSITIVE_MEMORY_KEY = re.compile(
    r"(?:password|passwd|passcode|api[_-]?key|token|secret|totp|credential|private[_-]?key)",
    re.IGNORECASE,
)


class MemoryItem:
    """Small value object returned by scoped memory queries."""

    def __init__(
        self,
        key: str,
        value: str,
        category: str = "general",
        confidence: float = 1.0,
        source: str = "explicit",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.key = key
        self.value = value
        self.category = category
        self.confidence = min(1.0, max(0.0, confidence))
        self.source = source
        self.created_at = now
        self.updated_at = now
        self.access_count = 0

    def touch(self) -> None:
        self.access_count += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()


def validate_memory_key(key: str) -> str:
    normalized = key.strip().lower()
    if normalized in AMBIGUOUS_MEMORY_KEYS:
        suggestions = {
            "name": "user_name or assistant_name",
            "language": "preferred_language",
            "style": "preferred_answer_style",
            "location": "user_location",
            "browser": "preferred_browser",
            "editor": "preferred_editor",
            "framework": "preferred_test_framework",
        }
        raise ValueError(
            f"Ambiguous memory key '{normalized}'; use {suggestions.get(normalized, 'a subject-specific key')}"
        )
    if not _KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Memory keys must be explicit snake_case identifiers, e.g. user_name or preferred_language"
        )
    return normalized


class SQLiteMemoryRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        initialize_wal(self.path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect_sqlite(self.path, foreign_keys=True)
        self.path.chmod(0o600)
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    principal_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    category TEXT NOT NULL,
                    importance TEXT NOT NULL CHECK (importance IN ('core', 'relevant')),
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (principal_id, key)
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            require_exact_table(
                db,
                "memories",
                (
                    ("principal_id", "TEXT", True, None, 1),
                    ("key", "TEXT", True, None, 2),
                    ("value", "TEXT", True, None, 0),
                    ("category", "TEXT", True, None, 0),
                    ("importance", "TEXT", True, None, 0),
                    ("confidence", "REAL", True, None, 0),
                    ("source", "TEXT", True, None, 0),
                    ("created_at", "TEXT", True, None, 0),
                    ("updated_at", "TEXT", True, None, 0),
                    ("last_used_at", "TEXT", False, None, 0),
                    ("access_count", "INTEGER", True, "0", 0),
                ),
                label="Memory",
            )
            require_exact_table(
                db,
                "episodes",
                (
                    ("id", "INTEGER", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_id", "TEXT", True, None, 0),
                    ("summary", "TEXT", True, None, 0),
                    ("tool_calls", "INTEGER", True, "0", 0),
                    ("source", "TEXT", True, None, 0),
                    ("created_at", "TEXT", True, None, 0),
                ),
                label="Memory episode",
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS memories_lookup
                   ON memories(principal_id, importance, category, updated_at DESC)"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS episodes_lookup
                   ON episodes(principal_id, session_id, created_at DESC)"""
            )
            require_index_columns(
                db,
                "memories_lookup",
                ("principal_id", "importance", "category", "updated_at"),
                label="Memory",
            )
            require_index_columns(
                db,
                "episodes_lookup",
                ("principal_id", "session_id", "created_at"),
                label="Memory episode",
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def store_memory(
        self,
        principal_id: str,
        key: str,
        value: str,
        *,
        category: str,
        importance: str,
        confidence: float,
        source: str,
    ) -> None:
        normalized = validate_memory_key(key)
        if importance not in _IMPORTANCE:
            raise ValueError("Memory importance must be core or relevant")
        category = category.strip().lower()
        if category not in _MEMORY_CATEGORIES:
            raise ValueError(
                "Memory category must be one of: "
                + ", ".join(sorted(_MEMORY_CATEGORIES))
            )
        if _SENSITIVE_MEMORY_KEY.search(normalized):
            raise ValueError("Credentials and authentication secrets must never be stored in memory")
        if not value or len(value) > 2000:
            raise ValueError("Memory value must contain 1-2000 characters")
        if importance == "core" and len(value) > 500:
            raise ValueError("Core memory values must be at most 500 characters")
        now = self._now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT importance FROM memories WHERE principal_id=? AND key=?",
                (principal_id, normalized),
            ).fetchone()
            if importance == "core" and (existing is None or existing[0] != "core"):
                count = db.execute(
                    "SELECT count(*) FROM memories WHERE principal_id=? AND importance='core'",
                    (principal_id,),
                ).fetchone()[0]
                if count >= 12:
                    raise ValueError("At most 12 core memories are allowed")
            db.execute(
                """
                INSERT INTO memories(
                    principal_id, key, value, category, importance, confidence,
                    source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(principal_id, key) DO UPDATE SET
                    value=excluded.value,
                    category=excluded.category,
                    importance=excluded.importance,
                    confidence=excluded.confidence,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    principal_id, normalized, value, category, importance,
                    max(0.0, min(1.0, confidence)), source, now, now,
                ),
            )

    def get_memory(self, principal_id: str, key: str) -> dict[str, Any] | None:
        normalized = validate_memory_key(key)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM memories WHERE principal_id=? AND key=?",
                (principal_id, normalized),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE memories SET access_count=access_count+1,
                   last_used_at=? WHERE principal_id=? AND key=?""",
                (self._now(), principal_id, normalized),
            )
            return dict(row)

    def list_memories(
        self,
        principal_id: str,
        *,
        importance: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE principal_id=?"
        params: list[Any] = [principal_id]
        if importance:
            sql += " AND importance=?"
            params.append(importance)
        sql += " ORDER BY confidence DESC, access_count DESC, updated_at DESC LIMIT ?"
        params.append(max(1, limit))
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def search_memories(
        self,
        principal_id: str,
        query: str,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        folded_query = query.casefold()
        terms = [term.casefold() for term in re.findall(r"[\w\-]+", query) if len(term) > 1]
        candidates = self.list_memories(principal_id, importance="relevant", limit=100)
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in candidates:
            haystack = f"{item['key']} {item['value']} {item['category']}".casefold()
            key = item["key"].casefold()
            value = item["value"].casefold()
            category = item["category"].casefold()
            score = sum(3 if term in key else 1 for term in terms if term in haystack)
            # Also support queries that contain a stored explicit value or key,
            # which matters for unsegmented CJK requests.
            if len(value) > 1 and value in folded_query:
                score += 2
            if key.replace("_", " ") in folded_query or key in folded_query:
                score += 3
            if len(category) > 1 and category in folded_query:
                score += 1
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], -float(pair[1]["confidence"])))
        return [item for _, item in scored[:limit]]

    def delete_memory(self, principal_id: str, key: str) -> bool:
        normalized = validate_memory_key(key)
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM memories WHERE principal_id=? AND key=?",
                (principal_id, normalized),
            )
            return cursor.rowcount > 0

    def clear_memories(self, principal_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM memories WHERE principal_id=?", (principal_id,))
            db.execute("DELETE FROM episodes WHERE principal_id=?", (principal_id,))

    def store_episode(
        self,
        principal_id: str,
        session_id: str,
        summary: str,
        *,
        tool_calls: int = 0,
        source: str = "explicit",
    ) -> None:
        if not summary or len(summary) > 4000:
            raise ValueError("Episode summary must contain 1-4000 characters")
        with self._connect() as db:
            db.execute(
                """INSERT INTO episodes(
                    principal_id, session_id, summary, tool_calls, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (principal_id, session_id, summary, tool_calls, source, self._now()),
            )

    def recall_episodes(
        self,
        principal_id: str,
        session_id: str,
        query: str = "",
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM episodes WHERE principal_id=? AND session_id=?"
        params: list[Any] = [principal_id, session_id]
        if query:
            sql += " AND summary LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, limit))
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]


def _to_item(row: dict[str, Any]) -> MemoryItem:
    item = MemoryItem(
        key=row["key"],
        value=row["value"],
        category=row["category"],
        confidence=float(row["confidence"]),
        source=row["source"],
    )
    item.created_at = row["created_at"]
    item.updated_at = row["updated_at"]
    item.access_count = int(row["access_count"])
    return item


class ScopedUserMemory:
    def __init__(self, repository: SQLiteMemoryRepository) -> None:
        self._repository = repository

    def store(
        self,
        key: str,
        value: str,
        category: str = "general",
        confidence: float = 1.0,
        source: str = "explicit",
        importance: str | None = None,
    ) -> None:
        scope = current_memory_scope()
        selected_importance = importance or (
            "core" if category in _CORE_CATEGORIES else "relevant"
        )
        self._repository.store_memory(
            scope.principal_id,
            key,
            value,
            category=category,
            importance=selected_importance,
            confidence=confidence,
            source=source,
        )

    def retrieve(self, key: str) -> MemoryItem | None:
        row = self._repository.get_memory(current_memory_scope().principal_id, key)
        return _to_item(row) if row else None

    def search(self, query: str, limit: int = 5) -> list[MemoryItem]:
        rows = self._repository.search_memories(
            current_memory_scope().principal_id,
            query,
            limit=limit,
        )
        return [_to_item(row) for row in rows]

    def delete(self, key: str) -> bool:
        return self._repository.delete_memory(current_memory_scope().principal_id, key)

    def clear(self) -> None:
        self._repository.clear_memories(current_memory_scope().principal_id)

    def get_all(self) -> list[MemoryItem]:
        rows = self._repository.list_memories(current_memory_scope().principal_id, limit=1000)
        return [_to_item(row) for row in rows]

    def build_context_string(self, query: str = "", max_core: int = 12, max_relevant: int = 5) -> str:
        principal = current_memory_scope().principal_id
        core = self._repository.list_memories(principal, importance="core", limit=max_core)
        relevant = self._repository.search_memories(principal, query, limit=max_relevant) if query else []
        selected = core + [row for row in relevant if row["key"] not in {item["key"] for item in core}]
        if not selected:
            return ""
        parts = ["## User Memory"]
        for row in selected:
            parts.append(f"- {row['key']}: {row['value']}")
        return "\n".join(parts)

    def __len__(self) -> int:
        return len(self._repository.list_memories(current_memory_scope().principal_id, limit=1000))


class ScopedEpisodicMemory:
    def __init__(self, repository: SQLiteMemoryRepository) -> None:
        self._repository = repository

    def store_episode(self, summary: str, session_id: str = "", tool_calls: int = 0) -> None:
        scope = current_memory_scope()
        self._repository.store_episode(
            scope.principal_id,
            session_id or scope.session_id,
            summary,
            tool_calls=tool_calls,
        )

    def recall(self, query: str = "", limit: int = 5) -> list[dict[str, Any]]:
        scope = current_memory_scope()
        return self._repository.recall_episodes(
            scope.principal_id,
            scope.session_id,
            query,
            limit=limit,
        )

    def build_context_string(self, query: str = "", limit: int = 3) -> str:
        if not query:
            return ""
        episodes = self.recall(query, limit=limit)
        if not episodes:
            return ""
        return "\n".join(
            ["## Relevant Session Episodes"]
            + [f"- {episode['summary']}" for episode in episodes]
        )

    def __len__(self) -> int:
        return len(self.recall(limit=1000))
