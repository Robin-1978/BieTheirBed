"""Bounded pause tool that emits activity heartbeats to prevent watchdog timeouts."""
from __future__ import annotations

import asyncio
from typing import Any

from knoa_platform.agent_runtime.tool_step import current_tool_step_context
from knoa_platform.tools.base import ToolBase, ToolEffect, ToolRisk


class SleepTool(ToolBase):
    name = "sleep"
    description = (
        "Pause execution for a specified duration (1 to 60 seconds) to wait for "
        "external asynchronous operations, short status updates, or API backoff. "
        "Emits progress heartbeats during sleep to avoid idle watchdog timeouts."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset()
    risk = ToolRisk.LOW

    def __init__(self, max_seconds: float = 60.0) -> None:
        self._max_seconds = max(0.01, float(max_seconds))

    async def execute(self, **kwargs: Any) -> Any:
        raw_seconds = kwargs.get("seconds", 5)
        try:
            requested = float(raw_seconds)
        except (TypeError, ValueError):
            return {"error": "Invalid seconds parameter: must be a number"}

        if requested <= 0:
            return {"error": "Seconds must be greater than 0"}

        actual_seconds = min(requested, self._max_seconds)
        reason = str(kwargs.get("reason", "")).strip()

        ctx = current_tool_step_context()
        remaining = actual_seconds
        interrupted = False

        while remaining > 0:
            if ctx is not None and ctx.cancellation.is_set():
                interrupted = True
                break
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step
            if ctx is not None and ctx.activity_notifier is not None:
                ctx.activity_notifier.set()

        slept = round(actual_seconds - remaining, 2)
        result: dict[str, Any] = {
            "slept_seconds": slept,
            "status": "interrupted" if interrupted else "completed",
        }
        if actual_seconds < requested:
            result["note"] = f"Requested {requested}s was capped to maximum allowed {self._max_seconds}s"
        if reason:
            result["reason"] = reason
        return result

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": f"Duration to sleep in seconds (1 to {int(self._max_seconds)}, default 5).",
                        "minimum": 1,
                        "maximum": self._max_seconds,
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short explanation for why waiting is required.",
                    },
                },
                "required": ["seconds"],
                "additionalProperties": False,
            },
        }
