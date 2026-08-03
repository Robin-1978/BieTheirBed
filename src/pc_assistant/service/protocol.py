"""Wire protocol for the PC Assistant service (JSON over WebSocket).

Client -> Server (requests):
    {"method": "run",     "id": 1, "params": {"input": "...", "session_id": ""}}
    {"method": "cancel",  "id": 2, "params": {"session_id": ""}}
    {"method": "confirm", "id": 3, "params": {"code": "...", "approved": true}}
    {"method": "status",  "id": 4}
    {"method": "command", "id": 5, "params": {"cmd": "/clear"}}

Server -> Client (responses/events):
    {"type": "event",           "run_id": 1, "data": {...AgentEvent...}}
    {"type": "confirm_request", "data": {"tool": "...", "args": {...}, "code": "..."}}
    {"type": "result",          "id": 1,    "data": {...}}
    {"type": "notify",          "data": {"task_id": "...", "message": "..."}}
    {"type": "error",           "id": 1,    "data": {"message": "..."}}
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ── Paths ─────────────────────────────────────────────────────────────

def _runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "pc-assistant"
    return Path.home() / ".local" / "run" / "pc-assistant"


SOCKET_PATH = _runtime_dir() / "service.sock"
PID_PATH = _runtime_dir() / "service.pid"
LOG_PATH = _runtime_dir() / "service.log"


# ── Client -> Server ──────────────────────────────────────────────────

class ClientMessage(BaseModel):
    """A request from a client to the service."""
    method: str
    id: int = 0
    params: dict[str, Any] = Field(default_factory=dict)

    def is_run(self) -> bool:
        return self.method == "run"

    def is_cancel(self) -> bool:
        return self.method == "cancel"

    def is_confirm(self) -> bool:
        return self.method == "confirm"

    @property
    def input_text(self) -> str:
        return self.params.get("input", "")

    @property
    def session_id(self) -> str:
        return self.params.get("session_id", "")


# ── Server -> Client ──────────────────────────────────────────────────

class ServerMessage(BaseModel):
    """A message from the service to a client."""
    type: str
    id: int = 0
    run_id: int = 0
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def event(cls, run_id: int, event_data: dict[str, Any]) -> ServerMessage:
        return cls(type="event", run_id=run_id, data=event_data)

    @classmethod
    def result(cls, msg_id: int, data: dict[str, Any]) -> ServerMessage:
        return cls(type="result", id=msg_id, data=data)

    @classmethod
    def error(cls, msg_id: int, message: str) -> ServerMessage:
        return cls(type="error", id=msg_id, data={"message": message})

    @classmethod
    def confirm_request(cls, tool: str, args: dict[str, Any], code: str) -> ServerMessage:
        return cls(type="confirm_request", data={"tool": tool, "args": args, "code": code})

    @classmethod
    def notify(cls, task_id: str, message: str) -> ServerMessage:
        return cls(type="notify", data={"task_id": task_id, "message": message})


def serialize(msg: ServerMessage) -> str:
    return msg.model_dump_json()


def deserialize_client(raw: str) -> ClientMessage:
    return ClientMessage.model_validate_json(raw)
