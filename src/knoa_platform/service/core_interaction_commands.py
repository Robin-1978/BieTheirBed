"""Provider-neutral HumanInteraction Core command handler."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from knoa_platform.interactions import HumanInteractionService
from knoa_platform.service.core_api import (
    HumanInteractionResolvedMessage,
    HumanInteractionSnapshot,
    ResolveHumanInteractionRequest,
)

Send = Callable[[Any], Awaitable[None]]


class HumanInteractionCommandHandler:
    def __init__(self, interactions: HumanInteractionService | None) -> None:
        self._interactions = interactions

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        if not isinstance(request, ResolveHumanInteractionRequest):
            return False
        if self._interactions is None:
            raise RuntimeError("HumanInteraction service is unavailable")
        interaction, changed = await self._interactions.resolve(
            principal,
            request.interaction_id,
            request.value,
            resolved_by="core_api",
        )
        await send(
            HumanInteractionResolvedMessage(
                request_id=request.request_id,
                interaction=HumanInteractionSnapshot.from_record(interaction),
                resolved=changed,
            )
        )
        return True
