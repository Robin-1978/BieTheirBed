"""Per-session agent state and an LRU session manager.

Enables concurrent, isolated conversations (CLI, Feishu, benchmark) without
sharing a single `ConversationManager`, plus per-session cancellation, rollback
and usage accounting.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pc_assistant.context.conversation import ConversationManager


@dataclass
class SessionState:
    session_id: str
    conversation: ConversationManager
    cancelled: bool = False
    status: str = "ready"
    tool_call_history: list[str] = field(default_factory=list)
    tool_task: asyncio.Task | None = None
    # A conversation is an ordered mutable transcript.  Runs for different
    # sessions may execute concurrently, but two runs for the same session
    # must not interleave snapshots, messages, tool state, or rollback.
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_iterations: int = 0
    turn_count: int = 0
    snapshot_len: int = -1
    last_outcome: str = "idle"
    last_access: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_access = time.monotonic()

    def mark_snapshot(self) -> None:
        self.snapshot_len = self.conversation.snapshot_len()

    def rollback_if_needed(self) -> None:
        """Truncate the conversation back to the turn snapshot (cancel/error)."""
        if self.snapshot_len >= 0:
            self.conversation.truncate_to(self.snapshot_len)
        self.snapshot_len = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cancelled": self.cancelled,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_iterations": self.total_iterations,
            "turn_count": self.turn_count,
            "last_outcome": self.last_outcome,
            "messages": len(self.conversation),
        }


class SessionManager:
    """LRU-bounded collection of per-session states."""

    def __init__(self, max_sessions: int = 100, on_drop: Callable[[str], None] | None = None) -> None:
        self._max = max(1, max_sessions)
        self._states: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._on_drop = on_drop

    def set_drop_callback(self, callback: Callable[[str], None] | None) -> None:
        self._on_drop = callback

    def get(self, session_id: str, system_prompt: str) -> SessionState:
        with self._lock:
            if session_id not in self._states:
                self._evict_locked()
                conv = ConversationManager()
                conv.set_system_context(system_prompt)
                self._states[session_id] = SessionState(
                    session_id=session_id,
                    conversation=conv,
                )
            state = self._states[session_id]
            state.touch()
            return state

    def _evict_locked(self) -> None:
        if len(self._states) < self._max:
            return
        oldest = sorted(
            self._states.values(),
            key=lambda s: s.last_access,
        )
        for state in oldest[: self._max // 2]:
            # The default (empty-id) session is pinned so its conversation is
            # never evicted out from under the CLI/TUI that owns it.
            if state.session_id == "":
                continue
            removed = self._states.pop(state.session_id, None)
            if removed is not None and self._on_drop is not None:
                self._on_drop(state.session_id)

    def drop(self, session_id: str) -> None:
        with self._lock:
            removed = self._states.pop(session_id, None)
            if removed is not None and self._on_drop is not None:
                self._on_drop(session_id)

    def stats(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._states.values()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)
