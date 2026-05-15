from __future__ import annotations

import subprocess
from typing import Any

from pc_assistant.platform_ import get_platform
from pc_assistant.tools.base import ToolBase


class ApplicationTool(ToolBase):
    name = "application"
    description = "Launch, list, search, and manage desktop applications"

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action")
        handlers = {
            "launch": self._launch,
            "list_running": self._list_running,
            "search": self._search,
            "info": self._info,
            "kill": self._kill,
        }
        handler = handlers.get(action)
        if handler is None:
            return {"error": f"Unknown application action: {action}"}
        return handler(kwargs)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["launch", "list_running", "search", "info", "kill"],
                        "description": "Action to perform",
                    },
                    "command": {"type": "string", "description": "Command to launch application"},
                    "name": {
                        "type": "string",
                        "description": "Process name to search for (case-insensitive, partial match)"
                    },
                    "pid": {"type": "integer", "description": "Process ID (for info or kill)"},
                },
                "required": ["action"],
            },
        }

    def _launch(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        command = kwargs.get("command", "")
        if not command:
            return {"error": "No command provided for launch"}
        try:
            current = get_platform()
            if current == "windows":
                proc = subprocess.Popen(command, shell=True, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            elif current == "macos":
                proc = subprocess.Popen(["open", "-a", command] if not command.startswith("/") else [command], start_new_session=True)
            else:
                proc = subprocess.Popen(command, shell=True, start_new_session=True)
            return {"success": True, "pid": proc.pid}
        except Exception as e:
            return {"error": str(e)}

    def _list_running(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            import psutil

            procs = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status", "create_time"]):
                try:
                    info = proc.info
                    procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return {"processes": procs, "count": len(procs)}
        except ImportError:
            return {"error": "psutil not installed"}

    def _search(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Search for processes by name (case-insensitive, partial match)."""
        name = kwargs.get("name", "").lower().strip()
        if not name:
            return {"error": "No process name provided for search"}

        try:
            import psutil

            matches = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status", "create_time", "cmdline"]):
                try:
                    info = proc.info
                    proc_name = info.get("name", "").lower()
                    if name in proc_name:
                        matches.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Sort by CPU usage
            matches.sort(key=lambda x: x.get("cpu_percent", 0) or 0, reverse=True)

            return {
                "matches": matches,
                "count": len(matches),
                "search_term": kwargs.get("name", ""),
                "message": f"Found {len(matches)} process(es) matching '{kwargs.get('name', '')}'",
            }
        except ImportError:
            return {"error": "psutil not installed"}

    def _info(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Get detailed info about a specific process."""
        pid = kwargs.get("pid")
        if pid is None:
            return {"error": "No pid provided"}

        try:
            import psutil

            proc = psutil.Process(int(pid))
            info = {
                "pid": proc.pid,
                "name": proc.name(),
                "status": proc.status(),
                "cpu_percent": proc.cpu_percent(interval=0.1),
                "memory_percent": proc.memory_percent(),
                "memory_info": proc.memory_info()._asdict(),
                "create_time": proc.create_time(),
                "cmdline": proc.cmdline(),
                "cwd": proc.cwd(),
                "username": proc.username(),
                "open_files": [f._asdict() for f in proc.open_files()][:10],  # Limit to 10 files
                "connections": len(proc.connections()),
                "threads": proc.num_threads(),
                "children": [{"pid": c.pid, "name": c.name()} for c in proc.children(recursive=True)][:10],
            }
            return {"process": info}
        except psutil.NoSuchProcess:
            return {"error": f"No process with pid {pid}"}
        except psutil.AccessDenied:
            return {"error": f"Access denied for pid {pid}"}
        except Exception as e:
            return {"error": str(e)}

    def _kill(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        pid = kwargs.get("pid")
        if pid is None:
            return {"error": "No pid provided"}
        try:
            import psutil

            proc = psutil.Process(int(pid))
            proc.terminate()
            return {"success": True, "message": f"Process {pid} terminated"}
        except psutil.NoSuchProcess:
            return {"error": f"No process with pid {pid}"}
        except psutil.AccessDenied:
            return {"error": f"Access denied killing pid {pid}"}
        except Exception as e:
            return {"error": str(e)}
