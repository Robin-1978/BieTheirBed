"""Observability — per-call LLM traces and per-turn metrics.

Writes append-only JSONL files (one line per record) and keeps a small in-memory
ring so the UI can render recent activity without re-reading files.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _identity_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


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
                self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._path.touch(exist_ok=True)
                self._path.chmod(0o600)
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
        principal_id: str,
        session_id: str,
        run_id: str,
        client_request_id: str,
        model: str,
        iteration: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        latency_ms: float = 0.0,
        ttft_ms: float = 0.0,
        finish_reason: str = "",
        tool_calls: int = 0,
        error: str = "",
        requested_max_tokens: int = 0,
        message_budget: int = 0,
        schema_tokens: int = 0,
        failover_used: bool = False,
    ) -> None:
        self.record({
            "kind": "llm_call",
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "principal_hash": _identity_hash(principal_id),
            "session_hash": _identity_hash(session_id),
            "run_hash": _identity_hash(run_id),
            "client_request_hash": _identity_hash(client_request_id),
            "model": model,
            "iteration": iteration,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cache_hit_ratio": (cached_tokens / prompt_tokens) if prompt_tokens else 0.0,
            "latency_ms": round(latency_ms, 1),
            "ttft_ms": round(ttft_ms, 1),
            "finish_reason": finish_reason,
            "tool_calls": tool_calls,
            "error": error,
            "requested_max_tokens": requested_max_tokens,
            "message_budget": message_budget,
            "schema_tokens": schema_tokens,
            "failover_used": failover_used,
        })


class TurnRecorder(JsonlRecorder):
    """Records one line per user turn / agent run."""

    def __init__(self, path: str = "logs/turns.jsonl", enabled: bool = True) -> None:
        super().__init__(path, enabled=enabled)

    def record_turn(
        self,
        *,
        principal_id: str,
        session_id: str,
        run_id: str,
        client_request_id: str,
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
            "principal_hash": _identity_hash(principal_id),
            "session_hash": _identity_hash(session_id),
            "run_hash": _identity_hash(run_id),
            "client_request_hash": _identity_hash(client_request_id),
            "input_chars": len(user_input),
            "outcome": outcome,
            "iterations": iterations,
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "elapsed_ms": round(elapsed_ms, 1),
            "evidence_required": evidence_required,
            "evidence_satisfied": evidence_satisfied,
        })
