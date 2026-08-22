"""User-facing notification semantics shared by channel adapters.

Channels decide how to render an intent; Core event types only decide what the
user needs to know. This keeps notification policy and wording from drifting
between App, Feishu, and future channels.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

NotificationIntentKind = Literal["result", "decision", "recovery"]


class NotificationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_type: str
    kind: NotificationIntentKind
    policy_key: Literal["completed", "failed", "cancelled", "waiting_approval"]


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


__all__ = ["NotificationIntent", "NotificationIntentKind", "notification_intent_for_event"]
