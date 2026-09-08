"""Strongly-typed data models for universal Knoa Action Cards (Server-Driven UI)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ActionCardLevel(str, Enum):
    """Urgency / severity level of the action card."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"


class ActionCardStatus(str, Enum):
    """Lifecycle state of the action card."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


@dataclass(slots=True)
class ActionCardSource:
    """Metadata describing the origin of the action card."""

    plugin_name: str
    agent_name: str | None = None
    session_id: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionCardSource:
        return cls(
            plugin_name=str(data.get("plugin_name", "system")),
            agent_name=data.get("agent_name"),
            session_id=data.get("session_id"),
            created_at=float(data.get("created_at", time.time())),
            expires_at=data.get("expires_at"),
        )


@dataclass(slots=True)
class KeyValueItem:
    """Single key-value pair for structured metadata display."""

    key: str
    value: str
    style: str = "default"  # default, bold, code, badge, muted

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "style": self.style}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyValueItem:
        return cls(
            key=str(data.get("key", "")),
            value=str(data.get("value", "")),
            style=str(data.get("style", "default")),
        )


@dataclass(slots=True)
class ActionCardBlock:
    """Base content block inside an action card."""

    type: str  # markdown, key_value, code_diff, callout, artifact_link
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionCardBlock:
        raw = dict(data)
        block_type = str(raw.pop("type", "markdown"))
        return cls(type=block_type, payload=raw)


@dataclass(slots=True)
class ActionCardInput:
    """Optional form input control for capturing human feedback/decisions."""

    id: str
    label: str
    input_type: str = "text"  # text, textarea, select, switch
    placeholder: str = ""
    default_value: Any = None
    options: list[dict[str, str]] = field(default_factory=list)  # for select: [{"label": "...", "value": "..."}]
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "input_type": self.input_type,
            "placeholder": self.placeholder,
            "default_value": self.default_value,
            "options": self.options,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionCardInput:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            input_type=str(data.get("input_type", "text")),
            placeholder=str(data.get("placeholder", "")),
            default_value=data.get("default_value"),
            options=list(data.get("options", [])),
            required=bool(data.get("required", False)),
        )


@dataclass(slots=True)
class ActionCardButton:
    """Actionable button declared by backend and rendered by universal clients."""

    id: str
    label: str
    style: str = "primary"  # primary, secondary, danger, outline
    action_type: str = "invoke_tool"  # invoke_tool, open_url, dismiss, custom
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    confirm_dialog: dict[str, str] | None = None  # {"title": "...", "message": "..."}
    include_form_inputs: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "style": self.style,
            "action_type": self.action_type,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "confirm_dialog": self.confirm_dialog,
            "include_form_inputs": self.include_form_inputs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionCardButton:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            style=str(data.get("style", "primary")),
            action_type=str(data.get("action_type", "invoke_tool")),
            tool_name=data.get("tool_name"),
            arguments=dict(data.get("arguments", {})),
            confirm_dialog=data.get("confirm_dialog"),
            include_form_inputs=bool(data.get("include_form_inputs", True)),
        )


@dataclass(slots=True)
class ActionCard:
    """Universal Server-Driven UI Action Card protocol for Knoa OS."""

    title: str
    card_id: str = field(default_factory=lambda: f"card_{uuid.uuid4().hex[:16]}")
    subtitle: str | None = None
    level: ActionCardLevel = ActionCardLevel.INFO
    status: ActionCardStatus = ActionCardStatus.PENDING
    source: ActionCardSource = field(default_factory=lambda: ActionCardSource(plugin_name="system"))
    blocks: list[ActionCardBlock] = field(default_factory=list)
    inputs: list[ActionCardInput] = field(default_factory=list)
    actions: list[ActionCardButton] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "card_id": self.card_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "level": self.level.value if isinstance(self.level, ActionCardLevel) else str(self.level),
            "status": self.status.value if isinstance(self.status, ActionCardStatus) else str(self.status),
            "source": self.source.to_dict(),
            "blocks": [b.to_dict() for b in self.blocks],
            "inputs": [i.to_dict() for i in self.inputs],
            "actions": [a.to_dict() for a in self.actions],
            "metadata": self.metadata,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionCard:
        source_data = data.get("source", {})
        source = (
            ActionCardSource.from_dict(source_data)
            if isinstance(source_data, dict)
            else ActionCardSource(plugin_name="system")
        )

        level_raw = data.get("level", "info")
        try:
            level = ActionCardLevel(level_raw)
        except ValueError:
            level = ActionCardLevel.INFO

        status_raw = data.get("status", "pending")
        try:
            status = ActionCardStatus(status_raw)
        except ValueError:
            status = ActionCardStatus.PENDING

        blocks = [
            ActionCardBlock.from_dict(b)
            for b in data.get("blocks", [])
            if isinstance(b, dict)
        ]

        inputs = [
            ActionCardInput.from_dict(i)
            for i in data.get("inputs", [])
            if isinstance(i, dict)
        ]

        actions = [
            ActionCardButton.from_dict(a)
            for a in data.get("actions", [])
            if isinstance(a, dict)
        ]

        return cls(
            card_id=str(data.get("card_id", f"card_{uuid.uuid4().hex[:16]}")),
            title=str(data.get("title", "")),
            subtitle=data.get("subtitle"),
            level=level,
            status=status,
            source=source,
            blocks=blocks,
            inputs=inputs,
            actions=actions,
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_json(cls, json_str: str) -> ActionCard:
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("ActionCard JSON must be an object")
        return cls.from_dict(data)
