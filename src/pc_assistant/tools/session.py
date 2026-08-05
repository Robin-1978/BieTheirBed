from __future__ import annotations

import os
import subprocess
from typing import Any

from pc_assistant.tools.base import ToolBase


class SessionTool(ToolBase):
    """Inspect or lock the current local graphical session.

    Unlock is intentionally excluded: remote unlock belongs exclusively to the
    authenticated channel broker (TOTP/passkey), never to the model tool set.
    """

    name = "session"
    description = "Inspect or lock the current local graphical desktop session"

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action", "status"))
        session_id = self._graphical_session()
        if not session_id:
            return {"error": "No local graphical session found"}
        if action == "status":
            return self._status(session_id)
        if action == "lock":
            result = subprocess.run(
                ["loginctl", "lock-session", session_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "lock rejected").strip()
                return {"error": detail[:200], "session_id": session_id}
            status = self._status(session_id)
            if status.get("locked") is not True:
                return {"error": "Lock request was not verified", **status}
            return {"success": True, **status}
        return {"error": f"Unknown session action: {action}"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["status", "lock"]},
                },
                "required": ["action"],
            },
        }

    def core_schema(self) -> dict[str, Any]:
        return self.schema()

    @staticmethod
    def _status(session_id: str) -> dict[str, Any]:
        result = subprocess.run(
            [
                "loginctl", "show-session", session_id,
                "-p", "Type", "-p", "Active", "-p", "State",
                "-p", "LockedHint", "-p", "Remote",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "session status unavailable").strip()
            return {"error": detail[:200], "session_id": session_id}
        values = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        return {
            "session_id": session_id,
            "type": values.get("Type", ""),
            "active": values.get("Active") == "yes",
            "state": values.get("State", ""),
            "locked": values.get("LockedHint") == "yes",
            "remote": values.get("Remote") == "yes",
        }

    @staticmethod
    def _graphical_session() -> str:
        explicit = os.environ.get("XDG_SESSION_ID", "").strip()
        if explicit:
            return explicit
        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        uid = str(os.getuid())
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[1] != uid:
                continue
            session_id = fields[0]
            status = SessionTool._status(session_id)
            if status.get("type") in {"x11", "wayland"} and not status.get("remote"):
                return session_id
        return ""
