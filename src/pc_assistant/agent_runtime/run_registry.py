"""Principal-owned run lifecycle and cancellation registry."""
from __future__ import annotations

import asyncio
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pc_assistant.agent_runtime.contracts import CancelResult, RuntimeScope


TerminalRunStatus = Literal["completed", "cancelled", "failed"]


@dataclass(frozen=True)
class RunHandle:
    """Opaque runtime capability for one Core-owned run."""

    run_id: str
    scope: RuntimeScope
    cancellation: asyncio.Event
    _capability: object

    @property
    def cancel_requested(self) -> bool:
        return self.cancellation.is_set()


@dataclass
class _RunEntry:
    handle: RunHandle
    status: Literal["running", "cancelling", "completed", "cancelled", "failed"] = "running"


class RunCapacityExceededError(RuntimeError):
    pass


class CoreRunRegistry:
    """Generate run identities and enforce principal-scoped lifecycle changes."""

    def __init__(
        self,
        *,
        run_id_factory: Callable[[], str] | None = None,
        max_active_runs: int = 32,
    ) -> None:
        if max_active_runs < 1:
            raise ValueError("Active run capacity must be at least one")
        self._run_id_factory = run_id_factory or (lambda: secrets.token_urlsafe(18))
        self._max_active_runs = max_active_runs
        self._capability = object()
        self._entries: dict[str, _RunEntry] = {}
        self._lock = threading.Lock()

    def start(self, scope: RuntimeScope) -> RunHandle:
        for _ in range(5):
            run_id = self._run_id_factory().strip()
            if not run_id:
                raise ValueError("run_id factory returned an empty identifier")
            with self._lock:
                if len(self._entries) >= self._max_active_runs:
                    raise RunCapacityExceededError("Global active run limit reached")
                if run_id in self._entries:
                    continue
                handle = RunHandle(
                    run_id=run_id,
                    scope=scope,
                    cancellation=asyncio.Event(),
                    _capability=self._capability,
                )
                self._entries[run_id] = _RunEntry(handle=handle)
                return handle
        raise RuntimeError("Could not allocate a unique run ID")

    def request_cancel(self, principal_id: str, run_id: str) -> CancelResult:
        principal = principal_id.strip()
        normalized_run_id = run_id.strip()
        if not principal or not normalized_run_id:
            return CancelResult(accepted=False, status="not_found")
        with self._lock:
            entry = self._entries.get(normalized_run_id)
            if entry is None or entry.handle.scope.principal_id != principal:
                return CancelResult(accepted=False, status="not_found")
            if entry.status in {"completed", "cancelled", "failed"}:
                return CancelResult(accepted=True, status=entry.status)
            entry.status = "cancelling"
            entry.handle.cancellation.set()
            return CancelResult(accepted=True, status="cancelling")

    def finish(self, handle: RunHandle, status: TerminalRunStatus) -> str:
        if handle._capability is not self._capability:
            raise PermissionError("Run handle was not issued by this registry")
        with self._lock:
            entry = self._entries.get(handle.run_id)
            if entry is None or entry.handle is not handle:
                raise PermissionError("Run handle is no longer registered")
            if entry.status in {"completed", "cancelled", "failed"}:
                if entry.status != status:
                    raise RuntimeError(
                        f"Run already terminated as {entry.status}; cannot terminate as {status}"
                    )
                return entry.status
            entry.status = status
            return status

    def status(self, principal_id: str, run_id: str) -> str | None:
        principal = principal_id.strip()
        normalized_run_id = run_id.strip()
        with self._lock:
            entry = self._entries.get(normalized_run_id)
            if entry is None or entry.handle.scope.principal_id != principal:
                return None
            return entry.status

    def resolve(self, principal_id: str, run_id: str) -> RunHandle | None:
        principal = principal_id.strip()
        normalized_run_id = run_id.strip()
        with self._lock:
            entry = self._entries.get(normalized_run_id)
            if entry is None or entry.handle.scope.principal_id != principal:
                return None
            return entry.handle

    def forget(self, handle: RunHandle) -> None:
        if handle._capability is not self._capability:
            raise PermissionError("Run handle was not issued by this registry")
        with self._lock:
            entry = self._entries.get(handle.run_id)
            if entry is None or entry.handle is not handle:
                return
            if entry.status not in {"completed", "cancelled", "failed"}:
                raise RuntimeError("Active run cannot be forgotten")
            self._entries.pop(handle.run_id, None)
