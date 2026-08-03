"""Observability — per-call LLM traces and per-turn metrics.

Writes append-only JSONL files (one line per record) and keeps a small in-memory
ring so the UI can render recent activity without re-reading files.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonlRecorder:
    """Thread-safe append-only JSONL sink with in-memory ring buffer."""

    def __init__(self, path: str = "logs/traces.jsonl", enabled: bool = True, ring: int = 200) -> None:
        self._path = Path(path)
        self._enabled = enabled
        self._lock = threading.Lock()
        self._ring: list[dict[str, Any]] = []
        self._ring_max = ring
        if enabled:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, entry: dict[str, Any]) -> None:
        if not self._enabled:
            return
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            self._ring.append(entry)
            if len(self._ring) > self._ring_max:
                del self._ring[: len(self._ring) - self._ring_max]
            try:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._ring[-limit:])


class LLMTraceRecorder(JsonlRecorder):
    """Records one line per LLM (stream) call."""

    def __init__(self, path: str = "logs/llm_calls.jsonl", enabled: bool = True) -> None:
        super().__init__(path, enabled=enabled)

    def record_call(
        self,
        *,
        session_id: str,
        model: str,
        iteration: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        ttft_ms: float = 0.0,
        finish_reason: str = "",
        tool_calls: int = 0,
        error: str = "",
    ) -> None:
        self.record({
            "kind": "llm_call",
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": session_id,
            "model": model,
            "iteration": iteration,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "latency_ms": round(latency_ms, 1),
            "ttft_ms": round(ttft_ms, 1),
            "finish_reason": finish_reason,
            "tool_calls": tool_calls,
            "error": error,
        })


class TurnRecorder(JsonlRecorder):
    """Records one line per user turn / agent run."""

    def __init__(self, path: str = "logs/turns.jsonl", enabled: bool = True) -> None:
        super().__init__(path, enabled=enabled)

    def record_turn(
        self,
        *,
        session_id: str,
        user_input: str,
        outcome: str,
        iterations: int,
        tool_calls: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        elapsed_ms: float = 0.0,
        evidence_required: bool = False,
        evidence_satisfied: bool = False,
    ) -> None:
        self.record({
            "kind": "turn",
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": session_id,
            "user_input": user_input[:200],
            "outcome": outcome,
            "iterations": iterations,
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "elapsed_ms": round(elapsed_ms, 1),
            "evidence_required": evidence_required,
            "evidence_satisfied": evidence_satisfied,
        })
