"""Cooperative cancellation primitive threaded through the agent loop."""
from __future__ import annotations

import threading


class CancelToken:
    """Thread-safe cooperative cancellation flag."""

    def __init__(self) -> None:
        self._cancelled = False
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def reset(self) -> None:
        with self._lock:
            self._cancelled = False
