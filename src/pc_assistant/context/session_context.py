"""Persistent, principal-owned rolling summaries for conversation Sessions."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pc_assistant.agent_runtime.contracts import RuntimeScope
from pc_assistant.context.compact import summarize_tool_turn
from pc_assistant.context.tags import (
    format_compacted_history,
    normalize_message_content,
)
from pc_assistant.context.token_estimate import TokenEstimator
from pc_assistant.exceptions import SessionNotFoundError
from pc_assistant.sqlite_connection import connect_sqlite, initialize_wal
from pc_assistant.sqlite_schema import require_exact_table, require_foreign_keys


class SessionContextRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_handle: str
    summary: str = ""
    covered_messages: int = Field(default=0, ge=0)
    updated_at: float = Field(default=0.0, ge=0.0)


class SessionContextRepository:
    """Own the durable summary without owning the full Session transcript."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser().resolve()
        initialize_wal(self._path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_session_contexts (
                    session_handle TEXT PRIMARY KEY
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    summary TEXT NOT NULL,
                    covered_messages INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            require_exact_table(
                db,
                "runtime_session_contexts",
                (
                    ("session_handle", "TEXT", False, None, 1),
                    ("summary", "TEXT", True, None, 0),
                    ("covered_messages", "INTEGER", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Runtime Session context",
            )
            require_foreign_keys(
                db,
                "runtime_session_contexts",
                (
                    (
                        "runtime_sessions",
                        "session_handle",
                        "session_handle",
                        "NO ACTION",
                        "CASCADE",
                    ),
                ),
                label="Runtime Session context",
            )

    @staticmethod
    def _owned(db: sqlite3.Connection, scope: RuntimeScope) -> None:
        row = db.execute(
            """SELECT 1 FROM runtime_sessions
               WHERE session_handle=? AND principal_id=?""",
            (scope.session_handle, scope.principal_id),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError()

    def load(self, scope: RuntimeScope) -> SessionContextRecord:
        with self._connect() as db:
            self._owned(db, scope)
            row = db.execute(
                """SELECT summary, covered_messages, updated_at
                   FROM runtime_session_contexts WHERE session_handle=?""",
                (scope.session_handle,),
            ).fetchone()
        if row is None:
            return SessionContextRecord(session_handle=scope.session_handle)
        return SessionContextRecord(
            session_handle=scope.session_handle,
            summary=str(row["summary"]),
            covered_messages=int(row["covered_messages"]),
            updated_at=float(row["updated_at"]),
        )

    def save(
        self,
        scope: RuntimeScope,
        *,
        summary: str,
        covered_messages: int,
    ) -> SessionContextRecord:
        if covered_messages < 0:
            raise ValueError("covered_messages must not be negative")
        now = time.time()
        with self._connect() as db:
            self._owned(db, scope)
            db.execute(
                """INSERT INTO runtime_session_contexts(
                       session_handle, summary, covered_messages, updated_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_handle) DO UPDATE SET
                       summary=excluded.summary,
                       covered_messages=excluded.covered_messages,
                       updated_at=excluded.updated_at""",
                (scope.session_handle, summary, covered_messages, now),
            )
        return SessionContextRecord(
            session_handle=scope.session_handle,
            summary=summary,
            covered_messages=covered_messages,
            updated_at=now,
        )


class SessionContextService:
    """Compact old turns on demand and expose one pinned Session summary."""

    def __init__(
        self,
        repository: SessionContextRepository,
        *,
        token_estimator: TokenEstimator | None = None,
        soft_token_limit: int = 32_000,
        keep_recent_turns: int = 4,
        max_summary_chars: int = 64_000,
    ) -> None:
        if soft_token_limit < 256:
            raise ValueError("soft_token_limit must be at least 256")
        if keep_recent_turns < 1:
            raise ValueError("keep_recent_turns must be positive")
        if max_summary_chars < 1000:
            raise ValueError("max_summary_chars must be at least 1000")
        self._repository = repository
        self._tokens = token_estimator or TokenEstimator()
        self._soft_token_limit = soft_token_limit
        self._keep_recent_turns = keep_recent_turns
        self._max_summary_chars = max_summary_chars

    def context(self, scope: RuntimeScope) -> str:
        record = self._repository.load(scope)
        if not record.summary.strip():
            return ""
        return format_compacted_history(
            record.summary.splitlines(),
            covered_messages=record.covered_messages,
            keep_recent=self._keep_recent_turns,
            source="session_rolling_summary",
        )

    def compact(
        self,
        scope: RuntimeScope,
        messages: tuple[dict[str, Any], ...],
    ) -> SessionContextRecord:
        current = self._repository.load(scope)
        if self._tokens.messages_tokens(list(messages)) <= self._soft_token_limit:
            return current
        cutoff = self._summary_cutoff(messages)
        if cutoff <= current.covered_messages:
            return current
        summary = self._summarize(messages[:cutoff])
        return self._repository.save(
            scope,
            summary=summary,
            covered_messages=cutoff,
        )

    def _summary_cutoff(self, messages: tuple[dict[str, Any], ...]) -> int:
        user_indexes = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
        ]
        if len(user_indexes) <= self._keep_recent_turns:
            return 0
        return user_indexes[-self._keep_recent_turns]

    def _summarize(self, messages: tuple[dict[str, Any], ...]) -> str:
        turns: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "user" and current:
                turns.append(current)
                current = []
            current.append(message)
        if current:
            turns.append(current)

        lines: list[str] = []
        for ordinal, turn in enumerate(turns, 1):
            users = [
                normalize_message_content(message.get("content") or "").strip()
                for message in turn
                if message.get("role") == "user"
            ]
            assistants = [
                normalize_message_content(message.get("content") or "").strip()
                for message in turn
                if message.get("role") == "assistant"
                and not message.get("tool_calls")
            ]
            if users:
                lines.append(f"Turn {ordinal} user: {self._bounded(users[-1], 1200)}")
            for fact in summarize_tool_turn(turn):
                lines.append(f"Turn {ordinal} tool: {self._bounded(fact, 1200)}")
            if assistants:
                lines.append(
                    f"Turn {ordinal} assistant: {self._bounded(assistants[-1], 1800)}"
                )
        summary = "\n".join(line for line in lines if line.strip())
        if len(summary) <= self._max_summary_chars:
            return summary
        return "[Earlier compacted context omitted]\n" + summary[-self._max_summary_chars :]

    @staticmethod
    def _bounded(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
