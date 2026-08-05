"""Unified scheduler: cron tasks, interval repeats, and one-shot timers.

Replaces the separate SchedulerTool + TimerTool with a single tool that
handles all time-based scheduling:

- Cron expressions: ``"0 9 * * *"``
- Interval repeats: ``"every 5m"``
- One-shot delays:  ``"in 30s"``, ``"in 2h30m"``
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from croniter import croniter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pc_assistant.tools.base import ToolBase
from pc_assistant.runtime import RuntimePaths


_DELAY_RE = re.compile(
    r"^in\s+(?:(\d+)\s*h)?(?:(\d+)\s*m)?(?:(\d+)\s*s)?$",
    re.IGNORECASE,
)
_INTERVAL_RE = re.compile(r"^every\s+(\d+)\s*([smhd])$", re.IGNORECASE)
_INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ScheduledTask:
    """A single scheduled item -- cron, interval, or one-shot delay."""

    def __init__(
        self,
        task_id: str,
        name: str,
        command: str,
        schedule: str,
        enabled: bool = True,
        last_run: datetime | None = None,
        next_run: datetime | None = None,
        run_count: int = 0,
        max_runs: int = 0,
        timeout: int = 300,
        message: str = "",
        callback: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.task_id = task_id
        self.name = name
        self.command = command
        self.schedule = schedule
        self.enabled = enabled
        self.last_run = last_run
        self.next_run = next_run
        self.run_count = run_count
        self.max_runs = max_runs
        self.timeout = timeout
        self.message = message
        self.callback = callback
        self._task: asyncio.Task | None = None
        self._is_running: bool = False
        self._paused_remaining: float | None = None

    # ── Schedule type detection ───────────────────────────────

    @property
    def schedule_type(self) -> str:
        if _DELAY_RE.match(self.schedule):
            return "delay"
        if _INTERVAL_RE.match(self.schedule):
            return "interval"
        return "cron"

    @property
    def is_one_shot(self) -> bool:
        return self.schedule_type == "delay"

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_paused(self) -> bool:
        return self._paused_remaining is not None

    # ── Next-run calculation ──────────────────────────────────

    def calculate_next_run(self) -> datetime | None:
        now = datetime.now()
        stype = self.schedule_type

        if stype == "delay":
            secs = self._parse_delay_seconds()
            return now + timedelta(seconds=secs) if secs and secs > 0 else None

        if stype == "interval":
            m = _INTERVAL_RE.match(self.schedule)
            if m:
                secs = int(m.group(1)) * _INTERVAL_UNITS.get(m.group(2).lower(), 60)
                return now + timedelta(seconds=secs)
            return None

        # cron
        try:
            return croniter(self.schedule, now).get_next(datetime)
        except (ValueError, KeyError):
            return None

    def _parse_delay_seconds(self) -> int:
        m = _DELAY_RE.match(self.schedule)
        if not m:
            return 0
        h = int(m.group(1) or 0)
        mi = int(m.group(2) or 0)
        s = int(m.group(3) or 0)
        return h * 3600 + mi * 60 + s

    # ── Pause / resume helpers ────────────────────────────────

    @property
    def remaining_seconds(self) -> float:
        if self._paused_remaining is not None:
            return self._paused_remaining
        if self.next_run is None:
            return 0
        return max(0, (self.next_run - datetime.now()).total_seconds())

    # ── Should-run check ──────────────────────────────────────

    def should_run(self) -> bool:
        if not self.enabled or self._is_running:
            return False
        if self.max_runs > 0 and self.run_count >= self.max_runs:
            return False
        if self.next_run is None:
            return False
        return datetime.now() >= self.next_run

    # ── Serialization ─────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "command": self.command,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "run_count": self.run_count,
            "max_runs": self.max_runs,
            "timeout": self.timeout,
            "message": self.message,
            "is_running": self._is_running,
            "schedule_type": self.schedule_type,
            "paused_remaining": self._paused_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledTask:
        def _to_local(dt_str: str | None) -> datetime | None:
            if not dt_str:
                return None
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(None).replace(tzinfo=None)
            return dt

        task = cls(
            task_id=data["task_id"],
            name=data["name"],
            command=data.get("command", ""),
            schedule=data["schedule"],
            enabled=data.get("enabled", True),
            last_run=_to_local(data.get("last_run")),
            next_run=_to_local(data.get("next_run")),
            run_count=data.get("run_count", 0),
            max_runs=data.get("max_runs", 0),
            timeout=data.get("timeout", 300),
            message=data.get("message", ""),
        )
        task._paused_remaining = data.get("paused_remaining")
        return task


# ── Tool ──────────────────────────────────────────────────────────────


class SchedulerTool(ToolBase):
    """Schedule tasks: cron, intervals, and one-shot timers.

    Examples:
    - ``"0 9 * * *"``   -- every day at 9:00 AM
    - ``"*/15 * * * *"`` -- every 15 minutes
    - ``"every 5m"``     -- every 5 minutes
    - ``"in 30s"``       -- countdown timer: 30 seconds from now
    - ``"in 2h30m"``     -- countdown timer: 2 hours 30 minutes
    """

    name = "scheduler"
    description = "Schedule recurring tasks, timers, and reminders (cron, intervals, one-shot delays)"
    is_side_effecting = True

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._notification_callback: Callable[[str, str], None] | None = None
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self._scheduler_task: asyncio.Task | None = None
        self._running = False
        self._agent: Any = None
        if self._storage_path is not None:
            self._initialize_storage()
            self._load()

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    def set_notification_callback(self, callback: Callable[[str, str], None]) -> None:
        self._notification_callback = callback

    def has_tasks(self) -> bool:
        """True when at least one scheduled task is registered."""
        return bool(self._tasks)

    def task_count(self) -> int:
        """Number of registered scheduled tasks."""
        return len(self._tasks)

    # ── Persistence ───────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if self._storage_path is None:
            self._storage_path = RuntimePaths.from_root().data / "assistant.db"
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._storage_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize_storage(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    command TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    last_run TEXT,
                    next_run TEXT,
                    run_count INTEGER NOT NULL,
                    max_runs INTEGER NOT NULL,
                    timeout INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    paused_remaining REAL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _load(self) -> None:
        try:
            with self._connect() as db:
                rows = db.execute("SELECT * FROM scheduled_tasks").fetchall()
            for row in rows:
                task = ScheduledTask.from_dict(dict(row))
                self._tasks[task.task_id] = task
        except (sqlite3.Error, OSError, KeyError, ValueError):
            pass

    def _save(self) -> None:
        task_ids = list(self._tasks)
        now = datetime.now().isoformat()
        with self._connect() as db:
            for task in self._tasks.values():
                db.execute(
                    """
                    INSERT INTO scheduled_tasks(
                        task_id, name, command, schedule, enabled, last_run,
                        next_run, run_count, max_runs, timeout, message,
                        paused_remaining, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        name=excluded.name,
                        command=excluded.command,
                        schedule=excluded.schedule,
                        enabled=excluded.enabled,
                        last_run=excluded.last_run,
                        next_run=excluded.next_run,
                        run_count=excluded.run_count,
                        max_runs=excluded.max_runs,
                        timeout=excluded.timeout,
                        message=excluded.message,
                        paused_remaining=excluded.paused_remaining,
                        updated_at=excluded.updated_at
                    """,
                    (
                        task.task_id, task.name, task.command, task.schedule,
                        int(task.enabled),
                        task.last_run.isoformat() if task.last_run else None,
                        task.next_run.isoformat() if task.next_run else None,
                        task.run_count, task.max_runs, task.timeout, task.message,
                        task._paused_remaining, now,
                    ),
                )
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                db.execute(
                    f"DELETE FROM scheduled_tasks WHERE task_id NOT IN ({placeholders})",
                    task_ids,
                )
            else:
                db.execute("DELETE FROM scheduled_tasks")

    # ── Execute dispatch ──────────────────────────────────────

    async def execute(self, **kwargs: Any) -> Any:
        if self._storage_path is None:
            self._initialize_storage()
            self._load()
        action = kwargs.get("action", "list")
        handlers = {
            "create": self._create_task,
            "add": self._create_task,
            "set": self._create_task,
            "list": self._list_tasks,
            "info": self._task_info,
            "enable": self._enable_task,
            "disable": self._disable_task,
            "delete": self._delete_task,
            "cancel": self._delete_task,
            "run": self._run_task,
            "start": self._start_scheduler,
            "stop": self._stop_scheduler,
            "status": self._scheduler_status,
            "pause": self._pause_task,
            "resume": self._resume_task,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: create/set, list, delete/cancel, pause, resume, start, stop, status."}
        result = handler(**kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    # ── Actions ───────────────────────────────────────────────

    async def _create_task(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs.get("task_name", kwargs.get("name", ""))
        command = kwargs.get("command", "")
        schedule = kwargs.get("schedule", "")
        message = kwargs.get("message", "")
        enabled = kwargs.get("enabled", True)
        max_runs = kwargs.get("max_runs", 0)
        timeout = kwargs.get("timeout", 300)

        if not schedule:
            return {"error": "schedule is required (cron, 'every Nm', or 'in Ns/Nm/Nh')"}
        if not command and not message:
            return {"error": "command or message is required"}

        import uuid
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        if not name:
            name = task_id

        task = ScheduledTask(
            task_id=task_id,
            name=name,
            command=command,
            schedule=schedule,
            enabled=enabled,
            max_runs=max_runs,
            timeout=timeout,
            message=message,
        )
        next_run = task.calculate_next_run()
        if next_run is None:
            return {"error": f"Invalid schedule: {schedule}. Use cron, 'every Nm', or 'in Ns/Nm/Nh'."}

        task.next_run = next_run
        if task.is_one_shot:
            task.max_runs = 1

        self._tasks[task_id] = task
        self._save()

        if task.is_one_shot and task.enabled:
            self._schedule_one_shot(task)

        return {
            "task_id": task_id,
            "name": name,
            "schedule": schedule,
            "schedule_type": task.schedule_type,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S"),
            "message": message,
            "description": f"Task '{name}' scheduled ({task.schedule_type}). Next: {next_run.strftime('%H:%M:%S')}",
        }

    def _list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        if not self._tasks:
            return {"tasks": [], "count": 0, "message": "No scheduled tasks"}

        tasks_data = []
        for task in self._tasks.values():
            info = task.to_dict()
            remaining = task.remaining_seconds
            info["remaining"] = self._fmt_duration(int(remaining))
            info["status"] = (
                "paused" if task.is_paused
                else "running" if task.is_running
                else "waiting"
            )
            tasks_data.append(info)

        tasks_data.sort(key=lambda x: x.get("next_run") or "")
        return {"tasks": tasks_data, "count": len(tasks_data), "scheduler_running": self._running}

    def _task_info(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        info = task.to_dict()
        info["remaining"] = self._fmt_duration(int(task.remaining_seconds))
        info["status"] = (
            "paused" if task.is_paused
            else "running" if task.is_running
            else "waiting"
        )
        return info

    def _enable_task(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        task.enabled = True
        task.next_run = task.calculate_next_run()
        self._save()
        return {"success": True, "task_id": task.task_id, "name": task.name, "next_run": str(task.next_run)}

    def _disable_task(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        task.enabled = False
        self._save()
        return {"success": True, "task_id": task.task_id, "name": task.name, "message": f"'{task.name}' disabled"}

    def _delete_task(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        if task._task and not task._task.done():
            task._task.cancel()
        del self._tasks[task.task_id]
        self._save()
        return {"success": True, "message": f"'{task.name}' deleted"}

    def _pause_task(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        if task.is_paused:
            return {"error": f"'{task.name}' is already paused"}
        remaining = task.remaining_seconds
        if task._task and not task._task.done():
            task._task.cancel()
            task._task = None
        task._paused_remaining = remaining
        self._save()
        return {"paused": task.task_id, "remaining": self._fmt_duration(int(remaining))}

    def _resume_task(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        if not task.is_paused:
            return {"error": f"'{task.name}' is not paused"}
        remaining = task._paused_remaining or 0
        task._paused_remaining = None
        task.next_run = datetime.now() + timedelta(seconds=remaining)
        task._task = asyncio.create_task(self._run_delay(task))
        self._save()
        return {"resumed": task.task_id, "remaining": self._fmt_duration(int(remaining))}

    async def _run_task(self, **kwargs: Any) -> dict[str, Any]:
        task = self._find_task(kwargs)
        if task is None:
            return {"error": self._not_found_msg(kwargs)}
        result = await self._execute_task(task)
        task.run_count += 1
        task.last_run = datetime.now()
        if not task.is_one_shot:
            task.next_run = task.calculate_next_run()
        self._save()
        return {"success": True, "task_id": task.task_id, "result": result}

    async def _start_scheduler(self, **kwargs: Any) -> dict[str, Any]:
        if self._running:
            return {"message": "Scheduler already running", "running": True}
        self._running = True
        for task in self._tasks.values():
            if task.is_one_shot and task.enabled and not task.is_running:
                self._schedule_one_shot(task)
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        return {"success": True, "message": "Scheduler started", "running": True}

    async def _stop_scheduler(self, **kwargs: Any) -> dict[str, Any]:
        if not self._running:
            return {"message": "Scheduler is not running", "running": False}
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        for task in self._tasks.values():
            if task.is_one_shot and task._task and not task._task.done():
                task._task.cancel()
                task._task = None
        return {"success": True, "message": "Scheduler stopped", "running": False}

    def _scheduler_status(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "running": self._running,
            "total": len(self._tasks),
            "enabled": len([t for t in self._tasks.values() if t.enabled]),
            "paused": len([t for t in self._tasks.values() if t.is_paused]),
            "pending": len([t for t in self._tasks.values() if t.should_run()]),
        }

    # ── Execution engine ──────────────────────────────────────

    async def _execute_task(self, task: ScheduledTask) -> dict[str, Any]:
        task._is_running = True
        try:
            if task.message and self._notification_callback:
                try:
                    self._notification_callback(task.task_id, task.message)
                except Exception:
                    pass

            if task.command and self._agent is not None:
                try:
                    result = await self._agent.run_simple(task.command)
                    return {"executed": True, "command": task.command, "result": result}
                except Exception as e:
                    return {"executed": False, "error": str(e)}

            if task.message and not task.command:
                return {"executed": True, "notified": True, "message": task.message}

            return {"executed": False, "message": "No agent or callback configured"}
        finally:
            task._is_running = False

    async def _run_delay(self, task: ScheduledTask) -> None:
        """Background coroutine for one-shot delay tasks."""
        try:
            if not task.enabled:
                return
            sleep_time = (
                task._paused_remaining
                if task._paused_remaining is not None
                else task.remaining_seconds
            )
            await asyncio.sleep(max(0, sleep_time))

            await self._execute_task(task)
            task.run_count += 1
            task.last_run = datetime.now()

            if task.task_id in self._tasks:
                del self._tasks[task.task_id]
                self._save()
        except asyncio.CancelledError:
            pass
        finally:
            if task._task is asyncio.current_task():
                task._task = None

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                for task in list(self._tasks.values()):
                    if task.should_run() and not task.is_one_shot:
                        await self._execute_task(task)
                        task.run_count += 1
                        task.last_run = datetime.now()
                        task.next_run = task.calculate_next_run()
                        self._save()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)

    def _schedule_one_shot(self, task: ScheduledTask) -> None:
        if task._task is not None and not task._task.done():
            return
        task._task = asyncio.create_task(self._run_delay(task))

    # ── Helpers ───────────────────────────────────────────────

    def _find_task(self, kwargs: dict[str, Any]) -> ScheduledTask | None:
        task_id = kwargs.get("task_id", "")
        name = kwargs.get("task_name", kwargs.get("name", ""))
        if task_id and task_id in self._tasks:
            return self._tasks[task_id]
        if name:
            for task in self._tasks.values():
                if task.name.lower() == name.lower():
                    return task
        return None

    def _not_found_msg(self, kwargs: dict[str, Any]) -> str:
        key = kwargs.get("task_id") or kwargs.get("task_name") or kwargs.get("name") or "?"
        return f"Task not found: {key}"

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        if seconds < 0:
            seconds = 0
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

    # ── Schemas ───────────────────────────────────────────────

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "add", "set", "list", "info", "enable", "disable",
                                 "delete", "cancel", "run", "start", "stop", "status", "pause", "resume"],
                        "description": "Action to perform",
                    },
                    "task_name": {"type": "string", "description": "Task name"},
                    "command": {"type": "string", "description": "Command to execute via agent"},
                    "schedule": {
                        "type": "string",
                        "description": "Schedule: cron ('0 9 * * *'), interval ('every 5m'), or delay ('in 30s', 'in 2h30m')",
                    },
                    "message": {"type": "string", "description": "Notification/reminder message"},
                    "task_id": {"type": "string", "description": "Task ID"},
                    "enabled": {"type": "boolean"},
                    "max_runs": {"type": "integer", "description": "Max runs (0=unlimited)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds"},
                },
                "required": ["action"],
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Schedule tasks, timers, reminders: cron, intervals, one-shot delays. "
                           "Use 'in 5m' for timers, 'every 1h' for intervals, '0 9 * * *' for cron.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "set", "list", "info", "delete", "cancel",
                                 "pause", "resume", "run", "start", "stop", "status"],
                    },
                    "task_name": {"type": "string"},
                    "command": {"type": "string"},
                    "schedule": {"type": "string"},
                    "message": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": ["action"],
            },
        }
