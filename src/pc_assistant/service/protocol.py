"""Wire protocol for the PC Assistant service (JSON over WebSocket).

Client -> Server (requests):
    {"method": "run",     "id": 1, "params": {"input": "...", "session_id": ""}}
    {"method": "cancel",  "id": 2, "params": {"session_id": ""}}
    {"method": "confirm", "id": 3, "params": {"code": "...", "approved": true}}
    {"method": "status",  "id": 4}
    {"method": "command", "id": 5, "params": {"cmd": "/clear", "session_id": ""}}

Server -> Client (responses/events):
    {"type": "event",           "run_id": 1, "data": {...AgentEvent...}}
    {"type": "confirm_request", "data": {"tool": "...", "args": {...}, "code": "..."}}
    {"type": "result",          "id": 1,    "data": {...}}
    {"type": "notify",          "data": {"task_id": "...", "message": "..."}}
    {"type": "error",           "id": 1,    "data": {"message": "..."}}
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ── Paths ─────────────────────────────────────────────────────────────

from pc_assistant.runtime import RuntimePaths

_PATHS = RuntimePaths.from_root()
SOCKET_PATH = _PATHS.socket
PID_PATH = _PATHS.pid
LOG_PATH = _PATHS.logs / "service.log"

# Upper bound for inbound/outbound websocket frames (bytes). The default
# (1 MiB) is too small: tool results such as reading a ~1 MiB screenshot
# produce bigger frames and silently drop the connection. 64 MiB is a safe
# ceiling for screenshot/process payloads.
WS_MAX_SIZE = 64 * 1024 * 1024


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

    @property
    def attachments(self) -> list[Any]:
        from pc_assistant.model_adapter.types import ImageAttachment

        raw = self.params.get("attachments", [])
        if not raw:
            return []
        attachments = [ImageAttachment.model_validate(a) for a in raw]
        if any(not attachment.attachment_id or attachment.data_url or attachment.path for attachment in attachments):
            raise ValueError("Run requests accept attachment_id references only; upload images first")
        return attachments

    @property
    def upload_attachment(self):
        from pc_assistant.model_adapter.types import ImageAttachment

        attachment = ImageAttachment.model_validate(self.params.get("attachment", {}))
        if attachment.attachment_id or not attachment.data_url or attachment.path:
            raise ValueError("Upload requests require one image data_url")
        return attachment


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
