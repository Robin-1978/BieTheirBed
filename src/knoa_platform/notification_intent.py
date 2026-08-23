"""User-facing notification semantics shared by channel adapters.

Channels decide how to render an intent; Core event types only decide what the
user needs to know. This keeps notification policy and wording from drifting
between App, Feishu, and future channels.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NotificationIntentKind = Literal["result", "decision", "recovery"]
NotificationCategory = Literal[
    "completed",
    "failed",
    "cancelled",
    "approval_required",
    "interaction_required",
    "node_offline",
    "update_required",
]


class NotificationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_type: str
    kind: NotificationIntentKind
    policy_key: Literal["completed", "failed", "cancelled", "waiting_approval"]


class NotificationIntentRecord(BaseModel):
    """Minimal durable user notification fact owned by Node Core."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    intent_id: str
    principal_id: str
    workspace_id: str = ""
    node_id: str = ""
    category: NotificationCategory
    work_kind: Literal["task", "conversation", "node", "release"]
    work_id: str
    execution_id: str = ""
    semantic_code: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    deep_link: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str
    priority: Literal["normal", "urgent"] = "normal"
    expires_at: float
    state: Literal["pending", "projected", "cancelled", "expired"]
    source_sequence: int
    created_at: float
    updated_at: float


def notification_intent_for_event(event_type: str) -> NotificationIntent | None:
    if event_type == "completed":
        return NotificationIntent(event_type=event_type, kind="result", policy_key="completed")
    if event_type == "failed":
        return NotificationIntent(event_type=event_type, kind="recovery", policy_key="failed")
    if event_type == "cancelled":
        return NotificationIntent(event_type=event_type, kind="recovery", policy_key="cancelled")
    if event_type in {"approval_requested", "interaction_requested"}:
        return NotificationIntent(
            event_type=event_type,
            kind="decision",
            policy_key="waiting_approval",
        )
    return None


__all__ = [
    "NotificationCategory",
    "NotificationIntent",
    "NotificationIntentKind",
    "NotificationIntentRecord",
    "notification_intent_for_event",
]
