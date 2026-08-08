"""Ordered public run-event construction."""
from __future__ import annotations

from pydantic import TypeAdapter

from pc_assistant.agent_runtime.contracts import RunEvent, RunEventType, RuntimeEventPayload


_RUN_ID_ADAPTER = TypeAdapter(str)


class RunEventSequencer:
    """Assign ordered event sequence numbers and enforce one terminal event."""

    def __init__(self, run_id: str) -> None:
        self._run_id = _RUN_ID_ADAPTER.validate_python(run_id, strict=True).strip()
        if not self._run_id or len(self._run_id) > 128:
            raise ValueError("run_id must contain 1-128 characters")
        self._event_seq = 0
        self._terminal = False

    @property
    def terminal(self) -> bool:
        return self._terminal

    def emit(
        self,
        event_type: RunEventType,
        payload: RuntimeEventPayload | None = None,
    ) -> RunEvent:
        if self._terminal:
            raise RuntimeError("Run already emitted a terminal event")
        self._event_seq += 1
        event = RunEvent(
            run_id=self._run_id,
            event_seq=self._event_seq,
            event_type=event_type,
            payload=payload or RuntimeEventPayload(),
        )
        if event.is_terminal:
            self._terminal = True
        return event
