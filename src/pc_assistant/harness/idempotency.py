"""Idempotency guard for side-effecting tool calls.

Maintains an in-memory + file-backed log keyed by
``hash(run_id, step_id, tool_name, sorted_args)``.  When a duplicate key is
seen the cached result is returned instead of re-executing.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any


_SENTINEL = object()


class IdempotencyLog:
    def __init__(
        self,
        storage_path: str | Path = "data/idempotency.json",
        max_entries: int = 500,
        ttl_seconds: float = 3600,
    ) -> None:
        self._storage_path = Path(storage_path)
        self._max = max_entries
        self._ttl = ttl_seconds
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    @staticmethod
    def make_key(
        run_id: str,
        step_id: int,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        raw = json.dumps(
            {"run": run_id, "step": step_id, "tool": tool_name, "args": arguments},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def check(self, key: str) -> Any:
        """Return the cached result if *key* exists and is not expired, else ``_SENTINEL``."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _SENTINEL
            if time.time() - entry["ts"] > self._ttl:
                del self._entries[key]
                return _SENTINEL
            return entry["result"]

    def record(self, key: str, result: Any) -> None:
        with self._lock:
            self._entries[key] = {"result": result, "ts": time.time()}
            self._evict_locked()
        self._save()

    def _evict_locked(self) -> None:
        if len(self._entries) <= self._max:
            return
        now = time.time()
        expired = [k for k, v in self._entries.items() if now - v["ts"] > self._ttl]
        for k in expired:
            del self._entries[k]
        if len(self._entries) > self._max:
            sorted_keys = sorted(self._entries, key=lambda k: self._entries[k]["ts"])
            for k in sorted_keys[: len(self._entries) - self._max]:
                del self._entries[k]

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._entries = data.get("entries", {})
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump({"entries": self._entries}, f, ensure_ascii=False)
        except OSError:
            pass

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        self._save()
