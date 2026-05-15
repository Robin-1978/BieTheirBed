from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pc_assistant.tools.base import ToolBase


class TimerInfo:
    def __init__(
        self,
        timer_id: str,
        duration_seconds: int,
        message: str,
        started_at: datetime,
        end_time: datetime | None = None,
        repeat: bool = False,
        repeat_interval: int = 0,
        callback: Callable[[], None] | None = None,
    ) -> None:
        self.timer_id = timer_id
        self.duration_seconds = duration_seconds
        self.message = message
        self.started_at = started_at
        self.end_time = end_time or datetime.now(timezone.utc)
        self.repeat = repeat
        self.repeat_interval = repeat_interval
        self.callback = callback
        self.task: asyncio.Task | None = None
        self._paused_remaining: int | None = None

    @property
    def remaining_seconds(self) -> int:
        if self._paused_remaining is not None:
            return self._paused_remaining
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return max(0, int(self.duration_seconds - elapsed))

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def is_paused(self) -> bool:
        return self._paused_remaining is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timer_id": self.timer_id,
            "duration_seconds": self.duration_seconds,
            "message": self.message,
            "started_at": self.started_at.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "repeat": self.repeat,
            "repeat_interval": self.repeat_interval,
            "paused_remaining": self._paused_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimerInfo":
        info = cls(
            timer_id=data["timer_id"],
            duration_seconds=data["duration_seconds"],
            message=data.get("message", ""),
            started_at=datetime.fromisoformat(data["started_at"]),
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            repeat=data.get("repeat", False),
            repeat_interval=data.get("repeat_interval", 0),
        )
        info._paused_remaining = data.get("paused_remaining")
        return info


class TimerTool(ToolBase):
    name = "timer"
    description = "Set countdown timers, reminders, and alarms with persistence and modification support"

    def __init__(self, storage_path: str = "data/timers.json") -> None:
        self._timers: dict[str, TimerInfo] = {}
        self._callback: Callable[[str, str], None] | None = None
        self._storage_path = Path(storage_path)
        self._load()

    def set_notification_callback(self, callback: Callable[[str, str], None]) -> None:
        self._callback = callback

    def _load(self) -> None:
        """Load timers from persistent storage."""
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for timer_data in data.get("timers", []):
                info = TimerInfo.from_dict(timer_data)
                # Only restore non-paused timers that should still be running
                if info._paused_remaining is None:
                    remaining = info.remaining_seconds
                    if remaining > 0:
                        # Timer should still be running - recreate task
                        self._timers[info.timer_id] = info
                else:
                    # Restore paused timer
                    self._timers[info.timer_id] = info
        except (json.JSONDecodeError, OSError, KeyError) as e:
            pass  # Ignore load errors

    def _save(self) -> None:
        """Save timers to persistent storage."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "timers": [info.to_dict() for info in self._timers.values()],
        }
        with open(self._storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "set")
        handlers = {
            "set": self._set_timer,
            "list": self._list_timers,
            "cancel": self._cancel_timer,
            "status": self._timer_status,
            "pause": self._pause_timer,
            "resume": self._resume_timer,
            "modify": self._modify_timer,
            "change": self._change_timer,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: set, list, cancel, status, pause, resume, modify."}
        result = handler(kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _set_timer(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        seconds = kwargs.get("seconds", 0)
        minutes = kwargs.get("minutes", 0)
        hours = kwargs.get("hours", 0)
        message = kwargs.get("message", "")
        repeat = kwargs.get("repeat", False)
        repeat_interval = kwargs.get("repeat_interval", 0)

        total_seconds = int(seconds) + int(minutes) * 60 + int(hours) * 3600

        if total_seconds <= 0:
            return {"error": "Timer duration must be positive. Provide seconds, minutes, or hours."}
        if total_seconds > 86400 * 7:  # Max 7 days
            return {"error": "Timer cannot exceed 7 days."}

        # Generate unique timer ID
        import uuid
        timer_id = f"timer_{uuid.uuid4().hex[:8]}"

        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds:
            parts.append(f"{seconds}s")
        duration_str = " ".join(parts) or f"{total_seconds}s"

        # Create timer info
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        end_time = now + timedelta(seconds=total_seconds)

        timer_info = TimerInfo(
            timer_id=timer_id,
            duration_seconds=total_seconds,
            message=message,
            started_at=now,
            end_time=end_time,
            repeat=repeat,
            repeat_interval=repeat_interval,
        )
        self._timers[timer_id] = timer_info
        self._save()

        # Start the actual timer task
        timer_info.task = asyncio.create_task(self._run_timer(timer_info))

        return {
            "timer_id": timer_id,
            "duration_seconds": total_seconds,
            "duration": duration_str,
            "message": message,
            "end_time": end_time.isoformat(),
            "repeat": repeat,
            "description": f"Timer '{timer_id}' set for {duration_str}." + (" (repeating)" if repeat else ""),
            "status": "running",
        }

    async def _run_timer(self, timer_info: TimerInfo) -> None:
        try:
            # Calculate actual sleep time
            if timer_info._paused_remaining is not None:
                sleep_time = timer_info._paused_remaining
            else:
                sleep_time = timer_info.remaining_seconds

            while True:
                await asyncio.sleep(sleep_time)

                # Send notification
                if self._callback:
                    try:
                        self._callback(timer_info.timer_id, timer_info.message)
                    except Exception:
                        pass

                # Check if repeating
                if timer_info.repeat and timer_info.repeat_interval > 0:
                    # Reset for next iteration
                    from datetime import timedelta
                    timer_info.started_at = datetime.now(timezone.utc)
                    timer_info._paused_remaining = None
                    sleep_time = timer_info.repeat_interval
                    # Update end time
                    timer_info.end_time = datetime.now(timezone.utc) + timedelta(seconds=timer_info.repeat_interval)
                    self._save()
                else:
                    # Remove from active timers
                    if timer_info.timer_id in self._timers:
                        del self._timers[timer_info.timer_id]
                        self._save()
                    break

        except asyncio.CancelledError:
            # Timer was cancelled - don't remove, might be paused
            pass

    def _list_timers(self, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._timers:
            return {"timers": [], "count": 0, "message": "No active timers"}

        timers_data = []
        for timer_id, info in self._timers.items():
            remaining = info.remaining_seconds
            parts = []
            h = remaining // 3600
            m = (remaining % 3600) // 60
            s = remaining % 60
            if h > 0:
                parts.append(f"{h}h")
            if m > 0:
                parts.append(f"{m}m")
            if s > 0 or not parts:
                parts.append(f"{s}s")
            remaining_str = " ".join(parts)

            # Calculate end time
            from datetime import timedelta
            end_dt = datetime.now(timezone.utc) + timedelta(seconds=remaining)
            end_time_str = end_dt.strftime("%H:%M:%S")

            timers_data.append({
                "timer_id": timer_id,
                "remaining_seconds": remaining,
                "remaining": remaining_str,
                "end_time": end_time_str,
                "message": info.message,
                "status": "paused" if info.is_paused else ("running" if info.is_running else "stopped"),
                "repeat": info.repeat,
                "total_seconds": info.duration_seconds,
            })

        # Sort by remaining time
        timers_data.sort(key=lambda x: x["remaining_seconds"])

        return {
            "timers": timers_data,
            "count": len(timers_data),
        }

    def _cancel_timer(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        timer_id = kwargs.get("timer_id", "")
        if not timer_id:
            return {"error": "timer_id is required for cancel action"}

        if timer_id not in self._timers:
            return {"error": f"Timer '{timer_id}' not found"}

        timer_info = self._timers[timer_id]
        if timer_info.task and not timer_info.task.done():
            timer_info.task.cancel()
            try:
                # Just let it be cancelled, no need to await
                pass
            except asyncio.CancelledError:
                pass

        del self._timers[timer_id]
        self._save()
        return {
            "cancelled": timer_id,
            "message": f"Timer '{timer_id}' has been cancelled.",
        }

    def _timer_status(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        timer_id = kwargs.get("timer_id", "")
        if not timer_id:
            # Return status of all timers
            return self._list_timers()

        if timer_id not in self._timers:
            return {"error": f"Timer '{timer_id}' not found"}

        info = self._timers[timer_id]
        remaining = info.remaining_seconds

        return {
            "timer_id": timer_id,
            "remaining_seconds": remaining,
            "remaining": self._format_duration(remaining),
            "message": info.message,
            "status": "paused" if info.is_paused else ("running" if info.is_running else "stopped"),
            "total_seconds": info.duration_seconds,
            "progress_percent": round((1 - remaining / info.duration_seconds) * 100, 1) if info.duration_seconds > 0 else 100,
            "repeat": info.repeat,
            "repeat_interval": info.repeat_interval if info.repeat else 0,
        }

    def _pause_timer(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        timer_id = kwargs.get("timer_id", "")
        if not timer_id:
            return {"error": "timer_id is required for pause action"}

        if timer_id not in self._timers:
            return {"error": f"Timer '{timer_id}' not found"}

        timer_info = self._timers[timer_id]
        if not timer_info.is_running:
            return {"error": f"Timer '{timer_id}' is not running"}

        remaining = timer_info.remaining_seconds
        timer_info.task.cancel()
        timer_info._paused_remaining = remaining
        timer_info.task = None
        self._save()

        return {
            "paused": timer_id,
            "remaining_seconds": remaining,
            "remaining": self._format_duration(remaining),
            "message": f"Timer '{timer_id}' paused.",
        }

    def _resume_timer(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        timer_id = kwargs.get("timer_id", "")
        if not timer_id:
            return {"error": "timer_id is required for resume action"}

        if timer_id not in self._timers:
            return {"error": f"Timer '{timer_id}' not found"}

        timer_info = self._timers[timer_id]
        if timer_info.is_running:
            return {"error": f"Timer '{timer_id}' is already running"}

        timer_info.started_at = datetime.now(timezone.utc)
        timer_info.duration_seconds = timer_info._paused_remaining or timer_info.duration_seconds
        timer_info._paused_remaining = None
        timer_info.task = asyncio.create_task(self._run_timer(timer_info))
        self._save()

        return {
            "resumed": timer_id,
            "remaining_seconds": timer_info.duration_seconds,
            "remaining": self._format_duration(timer_info.duration_seconds),
            "message": f"Timer '{timer_id}' resumed.",
        }

    def _modify_timer(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Modify an existing timer (change duration, message, etc.)."""
        timer_id = kwargs.get("timer_id", "")
        if not timer_id:
            return {"error": "timer_id is required for modify action"}

        if timer_id not in self._timers:
            return {"error": f"Timer '{timer_id}' not found"}

        timer_info = self._timers[timer_id]

        # Get new values
        seconds = kwargs.get("seconds")
        minutes = kwargs.get("minutes")
        hours = kwargs.get("hours")
        message = kwargs.get("message")
        repeat = kwargs.get("repeat")
        repeat_interval = kwargs.get("repeat_interval")

        changes = []

        # Calculate new duration if provided
        if seconds is not None or minutes is not None or hours is not None:
            s = int(seconds) if seconds is not None else 0
            m = int(minutes) if minutes is not None else 0
            h = int(hours) if hours is not None else 0
            new_duration = h * 3600 + m * 60 + s

            if new_duration <= 0:
                return {"error": "New duration must be positive"}

            timer_info.duration_seconds = new_duration
            if timer_info._paused_remaining is not None:
                timer_info._paused_remaining = new_duration
            changes.append(f"duration={self._format_duration(new_duration)}")

        # Update message
        if message is not None:
            timer_info.message = message
            changes.append(f"message='{message}'")

        # Update repeat settings
        if repeat is not None:
            timer_info.repeat = bool(repeat)
            changes.append(f"repeat={timer_info.repeat}")

        if repeat_interval is not None:
            timer_info.repeat_interval = int(repeat_interval)
            changes.append(f"repeat_interval={repeat_interval}s")

        self._save()

        return {
            "modified": timer_id,
            "changes": changes,
            "message": f"Timer '{timer_id}' modified: {', '.join(changes)}",
            "status": self._timer_status({"timer_id": timer_id}),
        }

    def _change_timer(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Alias for modify - change an existing timer."""
        return self._modify_timer(kwargs)

    def _format_duration(self, seconds: int) -> str:
        parts = []
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            parts.append(f"{h}h")
        if m > 0:
            parts.append(f"{m}m")
        if s > 0 or not parts:
            parts.append(f"{s}s")
        return " ".join(parts)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "list", "cancel", "status", "pause", "resume", "modify", "change"],
                        "description": "Action: 'set' to create, 'list' to see all, 'cancel' to stop, 'status' to check, 'pause' to pause, 'resume' to continue, 'modify' to change",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "Hours for the timer (for 'set' and 'modify' actions)",
                    },
                    "minutes": {
                        "type": "integer",
                        "description": "Minutes for the timer (for 'set' and 'modify' actions)",
                    },
                    "seconds": {
                        "type": "integer",
                        "description": "Seconds for the timer (for 'set' and 'modify' actions)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Reminder message",
                    },
                    "timer_id": {
                        "type": "string",
                        "description": "Timer ID (for 'cancel', 'status', 'pause', 'resume', 'modify' actions)",
                    },
                    "repeat": {
                        "type": "boolean",
                        "description": "Repeat timer (for 'set' action)",
                    },
                    "repeat_interval": {
                        "type": "integer",
                        "description": "Repeat interval in seconds (for 'set' and 'modify' actions)",
                    },
                },
                "required": ["action"],
            },
        }
