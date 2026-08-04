"""AgentLike protocol: the shared interface between Agent and ServiceClient.

Both ``Agent`` and ``ServiceClient`` satisfy this protocol, so consumers
like ``ChatApp`` can accept either without caring about the backend.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from pc_assistant.agent import AgentEvent

ConfirmFn = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]


@runtime_checkable
class AgentLike(Protocol):
    """Minimal interface shared by Agent and ServiceClient."""

    async def run(
        self,
        user_input: str,
        *,
        session_id: str = "",
        confirm_callback: ConfirmFn | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        ...

    async def cancel(self, session_id: str = "") -> None:
        ...

    async def health_check(self) -> bool:
        ...

    async def get_status(self) -> dict[str, Any]:
        ...
