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
