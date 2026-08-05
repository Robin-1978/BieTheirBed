from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pc_assistant.redaction import redact, redact_tool_parameters


class AuditLogger:
    def __init__(self, log_dir: str = "logs/audit") -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._entries: list[dict[str, Any]] = []

    def log(
        self,
        action: str,
        tool: str | None = None,
        parameters: dict[str, Any] | None = None,
        result: Any = None,
        allowed: bool = True,
        reason: str = "",
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": self._session_id,
            "action": action,
            "tool": tool,
            "parameters": redact_tool_parameters(tool, parameters),
            "result_summary": str(redact(result))[:500] if result is not None else None,
            "allowed": allowed,
            "reason": reason,
        }
        self._entries.append(entry)
        self._flush_entry(entry)

    def _flush_entry(self, entry: dict[str, Any]) -> None:
        log_file = self._log_dir / f"audit_{self._session_id}.jsonl"
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def query(
        self,
        action: str | None = None,
        tool: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            if action is not None and entry.get("action") != action:
                continue
            if tool is not None and entry.get("tool") != tool:
                continue
            if since is not None:
                entry_ts = entry.get("timestamp", "")
                try:
                    entry_dt = datetime.fromisoformat(entry_ts)
                except (ValueError, TypeError):
                    continue
                if entry_dt < since:
                    continue
            results.append(entry)
        return results

    def session_summary(self) -> dict[str, Any]:
        total = len(self._entries)
        action_counts: dict[str, int] = {}
        tool_counts: dict[str, int] = {}
        error_count = 0
        blocked_count = 0
        for entry in self._entries:
            action = entry.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
            tool = entry.get("tool")
            if tool is not None:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if not entry.get("allowed", True):
                blocked_count += 1
            result_summary = entry.get("result_summary") or ""
            if isinstance(result_summary, str) and result_summary.lower().startswith("error"):
                error_count += 1
        return {
            "session_id": self._session_id,
            "total_entries": total,
            "action_counts": action_counts,
            "tool_counts": tool_counts,
            "error_count": error_count,
            "blocked_count": blocked_count,
        }

    def clear(self) -> None:
        self._entries.clear()
