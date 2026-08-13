"""Stable identities shared by durable approval and tool commit records."""
from __future__ import annotations

import hashlib
import json

from knoa_platform.agent_runtime.tool_step import ProposedToolCall


def task_tool_step_id(task_id: str, call: ProposedToolCall) -> str:
    canonical = json.dumps(
        call.arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        f"{task_id}\0{call.call_id}\0{call.name}\0{canonical}".encode("utf-8")
    ).hexdigest()[:32]
