"""Small SQLite repository for restart-safe session transcripts."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class SessionTranscriptRepository:
    """Persist bounded, reference-only conversation messages by session ID."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_transcripts (
                    session_id TEXT PRIMARY KEY,
                    messages_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_sessions (
                    owner_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_context (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    covered_turns INTEGER NOT NULL,
                    source_message_count INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def load(self, session_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT messages_json FROM session_transcripts WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return []
        try:
            messages = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return []
        return messages if isinstance(messages, list) else []

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO session_transcripts(session_id, messages_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    messages_json = excluded.messages_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, payload, time.time()),
            )

    def delete(self, session_id: str) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("DELETE FROM session_transcripts WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_context WHERE session_id = ?", (session_id,))

    def load_context(self, session_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                """SELECT summary, covered_turns, source_message_count
                   FROM session_context WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "summary": str(row[0]),
            "covered_turns": int(row[1]),
            "source_message_count": int(row[2]),
        }

    def save_context(
        self,
        session_id: str,
        summary: str,
        covered_turns: int,
        source_message_count: int,
    ) -> None:
        with sqlite3.connect(self._path) as conn:
            if not summary:
                conn.execute("DELETE FROM session_context WHERE session_id = ?", (session_id,))
                return
            conn.execute(
                """
                INSERT INTO session_context(
                    session_id, summary, covered_turns, source_message_count, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    covered_turns = excluded.covered_turns,
                    source_message_count = excluded.source_message_count,
                    updated_at = excluded.updated_at
                """,
                (session_id, summary, covered_turns, source_message_count, time.time()),
            )

    def get_active(self, owner_id: str) -> str | None:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT session_id FROM active_sessions WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def set_active(self, owner_id: str, session_id: str) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO active_sessions(owner_id, session_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (owner_id, session_id, time.time()),
            )

    def latest_new(self, owner_id: str) -> str | None:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                """
                SELECT session_id FROM session_transcripts
                WHERE session_id LIKE ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (f"{owner_id}:new:%",),
            ).fetchone()
        return str(row[0]) if row else None
