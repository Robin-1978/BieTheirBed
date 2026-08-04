"""Confirmation gate shared by Agent, Verifier, TUI, Feishu and ServiceServer.

A confirmation gate is a callable ``(tool_name, args) -> bool``. ``True``
means the user explicitly approved the tool call, ``False`` (or an exception,
or a timeout) means denied. Every frontend (TUI prompt, Feishu card, service
round-trip) implements the same protocol so the agent layer never needs to
know which one is in use.

All frontends MUST fail closed: a timeout or a crashed gate is treated as a
denial, never as an approval.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

#: How long a confirmation prompt may stay open before it is treated as a
#: denial. Kept in one place so every frontend uses the same timeout.
CONFIRM_TIMEOUT = 120.0  # seconds

#: Backwards-compatible signature used by ``Agent.run`` and ``Verifier.verify``.
ConfirmFn = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]


@runtime_checkable
class ConfirmationGate(Protocol):
    """Protocol every confirm provider (TUI / Feishu / server) implements."""

    def __call__(self, tool_name: str, args: dict[str, Any]) -> bool | Awaitable[bool]:
        """Return True only when the user explicitly approved. Fail closed."""
        ...


async def resolve_confirm(
    gate: ConfirmationGate | None,
    tool_name: str,
    args: dict[str, Any],
) -> bool:
    """Invoke a confirmation gate, normalizing sync/async results to a bool.

    A ``None`` gate or any exception inside the gate resolves to ``False`` so
    the caller can always proceed fail-closed.
    """
    if gate is None:
        return False
    try:
        result = gate(tool_name, args)
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            return bool(await result)  # type: ignore[arg-type]
        return bool(result)
    except Exception:
        return False
