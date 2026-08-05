from __future__ import annotations

import time
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_calls: int = 60, window_seconds: int = 60) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._calls: dict[str, list[float]] = defaultdict(list)

    def _cleanup(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        self._calls[key] = [t for t in self._calls[key] if t > cutoff]

    def is_allowed(self, key: str = "default") -> bool:
        self._cleanup(key)
        if len(self._calls[key]) >= self._max_calls:
            return False
        self._calls[key].append(time.monotonic())
        return True

    def remaining(self, key: str = "default") -> int:
        self._cleanup(key)
        return max(0, self._max_calls - len(self._calls[key]))

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._calls.clear()
        else:
            self._calls.pop(key, None)
