from __future__ import annotations

import asyncio
import json
import re
from croniter import croniter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from pc_assistant.tools.base import ToolBase


class ScheduledTask:
    """Represents a scheduled task."""

    def __init__(
        self,
        task_id: str,
        name: str,
        command: str,
        schedule: str,  # cron expression or interval
        enabled: bool = True,
        last_run: datetime | None = None,
        next_run: datetime | None = None,
        run_count: int = 0,
        max_runs: int = 0,  # 0 = unlimited
        timeout: int = 300,
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
        self.callback = callback
        self._task: asyncio.Task | None = None
        self._is_running: bool = False

    def calculate_next_run(self) -> datetime | None:
        """Calculate the next run time based on schedule."""
        now = datetime.now(timezone.utc)

        if self._is_cron_schedule():
            try:
                cron = croniter(self.schedule, now)
                return cron.get_next(datetime)
            except (ValueError, KeyError):
                return None
        else:
            # Parse interval format like "every 5m", "every 1h", "every 1d"
            match = re.match(r"every\s+(\d+)\s*([smhd])", self.schedule.lower())
            if match:
                value = int(match.group(1))
                unit = match.group(2)
                intervals = {"s": 1, "m": 60, "h": 3600, "d": 86400}
                seconds = value * intervals.get(unit, 60)
                return now + timedelta(seconds=seconds)
        return None

    def _is_cron_schedule(self) -> bool:
        """Check if schedule is a cron expression."""
        return " " in self.schedule and not self.schedule.lower().startswith("every")

    def should_run(self) -> bool:
        """Check if task should run now."""
        if not self.enabled:
            return False
        if self._is_running:
            return False
        if self.max_runs > 0 and self.run_count >= self.max_runs:
            return False
        if self.next_run is None:
            return False
        now = datetime.now(timezone.utc)
        return now >= self.next_run

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
            "schedule_type": "cron" if self._is_cron_schedule() else "interval",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledTask":
        task = cls(
            task_id=data["task_id"],
            name=data["name"],
            command=data["command"],
            schedule=data["schedule"],
            enabled=data.get("enabled", True),
            last_run=datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None,
            next_run=datetime.fromisoformat(data["next_run"]) if data.get("next_run") else None,
            run_count=data.get("run_count", 0),
            max_runs=data.get("max_runs", 0),
            timeout=data.get("timeout", 300),
        )
        return task


class SchedulerTool(ToolBase):
    """
    Schedule tasks to run at specific times or intervals.
    Supports cron expressions and simple interval notation.

    Examples:
    - "0 9 * * *" = Every day at 9:00 AM
    - "*/15 * * * *" = Every 15 minutes
    - "every 5m" = Every 5 minutes
    - "every 1h" = Every hour
    - "every 1d" = Every day
    """

    name = "scheduler"
    description = "Schedule tasks to run at specific times or intervals (cron-like scheduling)"

    def __init__(self, storage_path: str = "data/scheduled_tasks.json") -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._callback: Callable[[str, str], Any] | None = None
        self._storage_path = Path(storage_path)
        self._scheduler_task: asyncio.Task | None = None
        self._running = False
        self._agent: Any = None  # Reference to Agent for task execution
        self._load()

    def set_agent(self, agent: Any) -> None:
        """Set the agent for task execution."""
        self._agent = agent

    def set_execute_callback(self, callback: Callable[[str, str], Any]) -> None:
        """Set callback for task execution results."""
        self._callback = callback

    def _load(self) -> None:
        """Load tasks from persistent storage."""
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for task_data in data.get("tasks", []):
                task = ScheduledTask.from_dict(task_data)
                # Recalculate next_run for loaded tasks
                task.next_run = task.calculate_next_run()
                self._tasks[task.task_id] = task
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    def _save(self) -> None:
        """Save tasks to persistent storage."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "tasks": [task.to_dict() for task in self._tasks.values()],
        }
        with open(self._storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "list")
        handlers = {
            "create": self._create_task,
            "add": self._create_task,
            "list": self._list_tasks,
            "info": self._task_info,
            "enable": self._enable_task,
            "disable": self._disable_task,
            "delete": self._delete_task,
            "run": self._run_task,
            "start": self._start_scheduler,
            "stop": self._stop_scheduler,
            "status": self._scheduler_status,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown action: {action}. Use: create, list, info, enable, disable, delete, run, start, stop, status."}
        result = handler(**kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "add", "list", "info", "enable", "disable", "delete", "run", "start", "stop", "status"],
                        "description": "Action to perform",
                    },
                    "task_name": {
                        "type": "string",
                        "description": "Task name (for create, info, enable, disable, delete)",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command or message to execute (for create)",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "Schedule: cron expression (e.g., '25 9 * * *' for 9:25 AM daily) or interval (e.g., 'every 5m', 'every 1h')",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID (for info, enable, disable, delete, run)",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable task (for create, enable, disable)",
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": "Maximum number of runs (0 = unlimited, for create)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Task timeout in seconds (for create, default: 300)",
                    },
                },
                "required": ["action"],
            },
        }

    async def _create_task(self, **kwargs: Any) -> dict[str, Any]:
        """Create a new scheduled task."""
        name = kwargs.get("task_name", "")
        command = kwargs.get("command", "")
        schedule = kwargs.get("schedule", "")
        enabled = kwargs.get("enabled", True)
        max_runs = kwargs.get("max_runs", 0)
        timeout = kwargs.get("timeout", 300)

        if not name:
            return {"error": "task_name is required"}
        if not command:
            return {"error": "command is required"}
        if not schedule:
            return {"error": "schedule is required"}

        # Validate schedule
        task = ScheduledTask(
            task_id=f"task_{len(self._tasks) + 1}",
            name=name,
            command=command,
            schedule=schedule,
            enabled=enabled,
            max_runs=max_runs,
            timeout=timeout,
        )
        next_run = task.calculate_next_run()
        if next_run is None:
            return {"error": f"Invalid schedule: {schedule}"}

        task.next_run = next_run
        self._tasks[task.task_id] = task
        self._save()

        return {
            "success": True,
            "task_id": task.task_id,
            "name": name,
            "schedule": schedule,
            "schedule_type": "cron" if task._is_cron_schedule() else "interval",
            "next_run": next_run.isoformat(),
            "message": f"Task '{name}' created. Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S')}",
        }

    def _list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        """List all scheduled tasks."""
        if not self._tasks:
            return {"tasks": [], "count": 0, "message": "No scheduled tasks"}

        tasks_data = []
        for task in self._tasks.values():
            info = task.to_dict()
            # Calculate remaining time
            if task.next_run:
                remaining = (task.next_run - datetime.now(timezone.utc)).total_seconds()
                info["next_run_in"] = f"{int(remaining)}s" if remaining > 0 else "now"
            tasks_data.append(info)

        # Sort by next_run
        tasks_data.sort(key=lambda x: x.get("next_run") or "")

        return {
            "tasks": tasks_data,
            "count": len(tasks_data),
            "running": self._running,
        }

    def _task_info(self, **kwargs: Any) -> dict[str, Any]:
        """Get detailed info about a task."""
        task_id = kwargs.get("task_id", "")
        name = kwargs.get("task_name", "")

        task = self._find_task(task_id, name)
        if task is None:
            return {"error": f"Task not found: {task_id or name}"}

        info = task.to_dict()
        if task.next_run:
            remaining = (task.next_run - datetime.now(timezone.utc)).total_seconds()
            info["next_run_in_seconds"] = int(remaining) if remaining > 0 else 0

        return info

    def _enable_task(self, **kwargs: Any) -> dict[str, Any]:
        """Enable a scheduled task."""
        task_id = kwargs.get("task_id", "")
        name = kwargs.get("task_name", "")

        task = self._find_task(task_id, name)
        if task is None:
            return {"error": f"Task not found: {task_id or name}"}

        task.enabled = True
        task.next_run = task.calculate_next_run()
        self._save()

        return {
            "success": True,
            "task_id": task.task_id,
            "name": task.name,
            "message": f"Task '{task.name}' enabled. Next run: {task.next_run}",
        }

    def _disable_task(self, **kwargs: Any) -> dict[str, Any]:
        """Disable a scheduled task."""
        task_id = kwargs.get("task_id", "")
        name = kwargs.get("task_name", "")

        task = self._find_task(task_id, name)
        if task is None:
            return {"error": f"Task not found: {task_id or name}"}

        task.enabled = False
        self._save()

        return {
            "success": True,
            "task_id": task.task_id,
            "name": task.name,
            "message": f"Task '{task.name}' disabled",
        }

    def _delete_task(self, **kwargs: Any) -> dict[str, Any]:
        """Delete a scheduled task."""
        task_id = kwargs.get("task_id", "")
        name = kwargs.get("task_name", "")

        task = self._find_task(task_id, name)
        if task is None:
            return {"error": f"Task not found: {task_id or name}"}

        del self._tasks[task.task_id]
        self._save()

        return {
            "success": True,
            "message": f"Task '{task.name}' deleted",
        }

    async def _run_task(self, **kwargs: Any) -> dict[str, Any]:
        """Manually run a task immediately."""
        task_id = kwargs.get("task_id", "")
        name = kwargs.get("task_name", "")

        task = self._find_task(task_id, name)
        if task is None:
            return {"error": f"Task not found: {task_id or name}"}

        # Execute the task
        result = await self._execute_task(task)

        # Update run count and next run
        task.run_count += 1
        task.last_run = datetime.now(timezone.utc)
        task.next_run = task.calculate_next_run()
        self._save()

        return {
            "success": True,
            "task_id": task.task_id,
            "name": task.name,
            "result": result,
            "next_run": task.next_run.isoformat() if task.next_run else None,
        }

    async def _execute_task(self, task: ScheduledTask) -> dict[str, Any]:
        """Execute a scheduled task using Agent if available."""
        task._is_running = True
        try:
            # If we have an agent, execute the command through it
            if self._agent is not None:
                try:
                    # Execute through agent - this will call LLM and tools
                    result = await self._agent.run_simple(task.command)
                    return {
                        "executed": True,
                        "command": task.command,
                        "result": result,
                        "executor": "agent"
                    }
                except Exception as e:
                    return {
                        "executed": False,
                        "command": task.command,
                        "error": str(e),
                        "executor": "agent"
                    }
            # Fallback to callback
            elif self._callback:
                result = self._callback(task.task_id, task.command)
                if asyncio.iscoroutine(result):
                    result = await result
                return {"executed": True, "command": task.command, "executor": "callback"}
            return {
                "executed": False,
                "command": task.command,
                "message": "No agent or callback registered for task execution",
                "executor": "none"
            }
        finally:
            task._is_running = False

    async def _start_scheduler(self, **kwargs: Any) -> dict[str, Any]:
        """Start the scheduler."""
        if self._running:
            return {"message": "Scheduler is already running", "running": True}

        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        return {
            "success": True,
            "message": "Scheduler started",
            "running": True,
            "tasks_count": len([t for t in self._tasks.values() if t.enabled]),
        }

    async def _stop_scheduler(self, **kwargs: Any) -> dict[str, Any]:
        """Stop the scheduler."""
        if not self._running:
            return {"message": "Scheduler is not running", "running": False}

        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        return {"success": True, "message": "Scheduler stopped", "running": False}

    def _scheduler_status(self, **kwargs: Any) -> dict[str, Any]:
        """Get scheduler status."""
        return {
            "running": self._running,
            "total_tasks": len(self._tasks),
            "enabled_tasks": len([t for t in self._tasks.values() if t.enabled]),
            "disabled_tasks": len([t for t in self._tasks.values() if not t.enabled]),
            "pending_tasks": len([t for t in self._tasks.values() if t.should_run()]),
        }

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                # Check each task
                for task in self._tasks.values():
                    if task.should_run():
                        await self._execute_task(task)
                        task.run_count += 1
                        task.last_run = datetime.now(timezone.utc)
                        task.next_run = task.calculate_next_run()
                        self._save()

                # Sleep for a bit
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)

    def _find_task(self, task_id: str, name: str) -> ScheduledTask | None:
        """Find a task by ID or name."""
        if task_id and task_id in self._tasks:
            return self._tasks[task_id]
        if name:
            for task in self._tasks.values():
                if task.name.lower() == name.lower():
                    return task
        return None
