"""Unified scheduler: cron tasks, interval repeats, and one-shot timers.

Replaces the separate SchedulerTool + TimerTool with a single tool that
handles all time-based scheduling:

- Cron expressions: ``"0 9 * * *"``
- Interval repeats: ``"every 5m"``
- One-shot delays:  ``"in 30s"``, ``"in 2h30m"``
"""
from __future__ import annotations

import asyncio
import contextvars
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

# Bound by Agent for the duration of a scheduler tool call.  This keeps
# channel/session routing out of the model-visible schema while allowing a
# scheduled run to return to the conversation that created it.
_CURRENT_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "pc_assistant_scheduler_session", default=""
)


def bind_scheduler_session(session_id: str):
    return _CURRENT_SESSION_ID.set(session_id or "")


def reset_scheduler_session(token: contextvars.Token[str]) -> None:
    _CURRENT_SESSION_ID.reset(token)


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
        session_id: str = "",
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
        self.session_id = session_id
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
            "is_running": self._is_running,
            "schedule_type": self.schedule_type,
            "paused_remaining": self._paused_remaining,
            "session_id": self.session_id,
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
            session_id=data.get("session_id", ""),
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

    name = "schedule"
    description = "Set a timed reminder or recurring task."
    is_side_effecting = True

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._result_callback: Callable[[ScheduledTask, dict[str, Any]], Any] | None = None
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self._scheduler_task: asyncio.Task | None = None
        self._running = False
        self._agent: Any = None
        if self._storage_path is not None:
            self._initialize_storage()
            self._load()

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    def set_result_callback(self, callback: Callable[[ScheduledTask, dict[str, Any]], Any]) -> None:
        """Deliver completed Agent runs to the owning channel/session."""
        self._result_callback = callback

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
                    session_id TEXT NOT NULL DEFAULT '',
                    paused_remaining REAL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(scheduled_tasks)")}
            if "message" in columns:
                # Forward-only schema: discard old notification-only rows and
                # remove the obsolete message column entirely.
                db.execute("DROP TABLE IF EXISTS scheduled_tasks_v2")
                db.execute(
                    """
                    CREATE TABLE scheduled_tasks_v2 (
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
                        session_id TEXT NOT NULL DEFAULT '',
                        paused_remaining REAL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                session_expr = "session_id" if "session_id" in columns else "''"
                db.execute(
                    f"""
                    INSERT INTO scheduled_tasks_v2(
                        task_id, name, command, schedule, enabled, last_run,
                        next_run, run_count, max_runs, timeout, session_id,
                        paused_remaining, updated_at
                    )
                    SELECT task_id, name, command, schedule, enabled, last_run,
                           next_run, run_count, max_runs, timeout, {session_expr},
                           paused_remaining, updated_at
                    FROM scheduled_tasks
                    WHERE trim(command) <> ''
                    """
                )
                db.execute("DROP TABLE scheduled_tasks")
                db.execute("ALTER TABLE scheduled_tasks_v2 RENAME TO scheduled_tasks")
                columns = {row[1] for row in db.execute("PRAGMA table_info(scheduled_tasks)")}
            if "session_id" not in columns:
                db.execute("ALTER TABLE scheduled_tasks ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")

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
                        next_run, run_count, max_runs, timeout,
                        paused_remaining, session_id, updated_at
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
                        paused_remaining=excluded.paused_remaining,
                        session_id=excluded.session_id,
                        updated_at=excluded.updated_at
                    """,
                    (
                        task.task_id, task.name, task.command, task.schedule,
                        int(task.enabled),
                        task.last_run.isoformat() if task.last_run else None,
                        task.next_run.isoformat() if task.next_run else None,
                        task.run_count, task.max_runs, task.timeout,
                        task._paused_remaining, task.session_id, now,
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
            "list": self._list_tasks,
            "info": self._task_info,
            "delete": self._delete_task,
            "run": self._run_task,
            "start": self._start_scheduler,
            "stop": self._stop_scheduler,
            "status": self._scheduler_status,
            "pause": self._pause_task,
            "resume": self._resume_task,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: create, list, info, delete, run, pause, resume."}
        result = handler(**kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    # ── Actions ───────────────────────────────────────────────

    async def _create_task(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs.get("name", kwargs.get("task_name", ""))
        # ``task`` and ``when`` are the model-facing names. Keep the older
        # storage keys as an internal read path for persisted tasks/tests.
        command = kwargs.get("task", kwargs.get("command", ""))
        schedule = kwargs.get("when", kwargs.get("schedule", ""))
        enabled = kwargs.get("enabled", True)
        max_runs = kwargs.get("max_runs", 0)
        timeout = kwargs.get("timeout_seconds", kwargs.get("timeout", 300))

        if not schedule:
            return {
                "error": "when is required",
                "instruction": "For create, provide task and when. Example: task='check weather', when='in 2m'.",
            }
        if not command:
            return {
                "error": "task is required",
                "instruction": "Provide task (what the Agent should do) and when (for example: 'in 2m').",
            }

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
            session_id=_CURRENT_SESSION_ID.get(),
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
            result_payload: dict[str, Any]
            if task.command and self._agent is not None:
                try:
                    final_answer = ""
                    errors: list[str] = []
                    artifacts: list[dict[str, Any]] = []
                    async for event in self._agent.run(task.command, session_id=task.session_id):
                        if event.type == "final_answer":
                            final_answer = event.content
                        elif event.type in {"error", "cancelled", "iteration_limit"}:
                            errors.append(event.content)
                        elif event.type == "artifact" and event.artifact is not None:
                            artifacts.append(event.artifact.model_dump())
                    result_payload = {
                        "executed": not bool(errors),
                        "command": task.command,
                        "result": final_answer or (errors[-1] if errors else ""),
                        "artifacts": artifacts,
                    }
                except Exception as e:
                    result_payload = {"executed": False, "error": str(e)}

            else:
                result_payload = {"executed": False, "error": "No Agent or task prompt configured"}

            if self._result_callback is not None:
                try:
                    delivered = self._result_callback(task, result_payload)
                    if asyncio.iscoroutine(delivered):
                        await delivered
                except Exception:
                    pass
            return result_payload
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
                        "enum": ["create", "list", "info", "delete", "run", "pause", "resume"],
                        "description": "Action to perform",
                    },
                    "task": {"type": "string", "description": "Agent instruction to run later"},
                    "when": {
                        "type": "string",
                        "description": "Schedule: cron ('0 9 * * *'), interval ('every 5m'), or delay ('in 30s', 'in 2h30m')",
                    },
                    "name": {"type": "string", "description": "Optional task name"},
                    "task_id": {"type": "string", "description": "Task ID"},
                    "enabled": {"type": "boolean"},
                    "max_runs": {"type": "integer", "description": "Max runs (0=unlimited)"},
                    "timeout_seconds": {"type": "integer", "description": "Agent timeout seconds"},
                },
                "required": ["action"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "info", "delete", "run", "pause", "resume"],
                        "description": "create needs task + when; other actions use task_id",
                    },
                    "task": {"type": "string", "description": "Agent instruction, not a shell command"},
                    "when": {"type": "string", "description": "in 5m | every 1h | cron"},
                    "name": {"type": "string"},
                    "task_id": {"type": "string", "description": "task ID"},
                    "enabled": {"type": "boolean"},
                    "max_runs": {"type": "integer", "description": "0 means unlimited"},
                    "timeout_seconds": {"type": "integer", "description": "Agent timeout seconds"},
                },
                "required": ["action"],
            },
        }
