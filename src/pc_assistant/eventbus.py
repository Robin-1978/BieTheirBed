"""Pub/sub event bus for decoupled agent components.

Subscribers register for specific event types (or ``"*"`` for all).  When the
agent emits an ``AgentEvent``, the bus dispatches it to all matching
subscribers asynchronously.

Usage::

    bus = EventBus()
    bus.on("tool_call", my_handler)       # specific event type
    bus.on("*", audit_all_events)         # wildcard: all events
    await bus.emit(event)                 # non-blocking fan-out
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe *handler* to *event_type*.  Use ``"*"`` for all events."""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe *handler* from *event_type*."""
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    async def emit(self, event: Any) -> None:
        """Dispatch *event* to all matching subscribers (non-blocking)."""
        event_type = getattr(event, "type", "")
        handlers = list(self._handlers.get(event_type, []))
        handlers.extend(self._handlers.get("*", []))

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("EventBus handler %s raised: %s", handler.__name__, exc)

    def clear(self) -> None:
        self._handlers.clear()

    @property
    def subscriber_count(self) -> int:
        return sum(len(h) for h in self._handlers.values())
