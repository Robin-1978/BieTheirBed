"""User-facing status semantics shared by every work surface.

Conversation turns and product tasks keep their own domain state.  This module
only translates those states into the small vocabulary a person needs in order
to understand what Knoa is doing and what they can do next.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

UserWorkStatus = Literal[
    "queued",
    "working",
    "waiting_for_you",
    "completed",
    "failed",
    "paused",
    "cancelled",
]


class UserWorkStatusInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: UserWorkStatus
    terminal: bool
    requires_user: bool
    recoverable: bool
    recommended_action: Literal[
        "wait",
        "respond",
        "retry",
        "resume",
        "none",
    ]


def task_work_status(state: str, *, pending_approval_count: int = 0) -> UserWorkStatusInfo:
    """Map a durable Task state to user intent without leaking internals."""
    if pending_approval_count > 0 or state == "waiting_approval":
        return UserWorkStatusInfo(
            status="waiting_for_you",
            terminal=False,
            requires_user=True,
            recoverable=True,
            recommended_action="respond",
        )
    if state in {"queued", "running"}:
        return UserWorkStatusInfo(
            status="queued" if state == "queued" else "working",
            terminal=False,
            requires_user=False,
            recoverable=False,
            recommended_action="wait",
        )
    if state == "paused":
        return UserWorkStatusInfo(
            status="paused",
            terminal=False,
            requires_user=True,
            recoverable=True,
            recommended_action="resume",
        )
    if state == "completed":
        return UserWorkStatusInfo(
            status="completed",
            terminal=True,
            requires_user=False,
            recoverable=False,
            recommended_action="none",
        )
    if state == "cancelled":
        return UserWorkStatusInfo(
            status="cancelled",
            terminal=True,
            requires_user=False,
            recoverable=True,
            recommended_action="retry",
        )
    return UserWorkStatusInfo(
        status="failed",
        terminal=True,
        requires_user=True,
        recoverable=True,
        recommended_action="retry",
    )


def turn_work_status(state: str) -> UserWorkStatusInfo:
    """Map a Conversation turn state to the same user vocabulary as Tasks."""
    return task_work_status(state)


def product_task_work_status(
    task_state: str,
    execution_state: str | None,
    *,
    pending_approval_count: int = 0,
) -> UserWorkStatusInfo | None:
    """Map a durable product task to work status.

    Definition lifecycle (active/paused/archived) is deliberately kept
    separate from execution state. Archived tasks have no active work status;
    callers can still render their lifecycle state directly.
    """
    if task_state == "archived":
        return None
    if task_state == "paused":
        return task_work_status("paused")
    return task_work_status(
        execution_state or "queued",
        pending_approval_count=pending_approval_count,
    )


__all__ = [
    "UserWorkStatus",
    "UserWorkStatusInfo",
    "product_task_work_status",
    "task_work_status",
    "turn_work_status",
]
