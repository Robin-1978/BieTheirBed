"""Bounded live fan-out layered on the persistent Task event journal."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pc_assistant.tasks.models import TaskEvent


class TaskSubscriptionOverflowError(ConnectionError):
    def __init__(self, last_event_seq: int) -> None:
        self.last_event_seq = last_event_seq
        super().__init__("Task subscription buffer overflow")


@dataclass(eq=False)
class TaskEventSubscription:
    task_id: str
    queue: asyncio.Queue[TaskEvent | Exception]
    _hub: TaskEventHub
    _closed: bool = False

    async def receive(self) -> TaskEvent:
        item = await self.queue.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._hub.unsubscribe(self)


class TaskEventHub:
    """Publish committed events; never acts as the source of truth."""

    def __init__(self, *, subscriber_capacity: int = 256) -> None:
        if subscriber_capacity < 1:
            raise ValueError("Task subscriber capacity must be at least one")
        self._capacity = subscriber_capacity
        self._subscribers: dict[str, set[TaskEventSubscription]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, task_id: str) -> TaskEventSubscription:
        normalized = task_id.strip()
        if not normalized:
            raise ValueError("task_id must not be empty")
        subscription = TaskEventSubscription(
            task_id=normalized,
            queue=asyncio.Queue(maxsize=self._capacity),
            _hub=self,
        )
        async with self._lock:
            self._subscribers.setdefault(normalized, set()).add(subscription)
        return subscription

    async def unsubscribe(self, subscription: TaskEventSubscription) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(subscription.task_id)
            if subscribers is None:
                return
            subscribers.discard(subscription)
            if not subscribers:
                self._subscribers.pop(subscription.task_id, None)

    async def publish(self, event: TaskEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers.get(event.task_id, ()))
            overflowed: list[TaskEventSubscription] = []
            for subscription in subscribers:
                try:
                    subscription.queue.put_nowait(event)
                except asyncio.QueueFull:
                    overflowed.append(subscription)
            for subscription in overflowed:
                subscribers_for_task = self._subscribers.get(subscription.task_id)
                if subscribers_for_task is not None:
                    subscribers_for_task.discard(subscription)
                    if not subscribers_for_task:
                        self._subscribers.pop(subscription.task_id, None)
                subscription._closed = True
                while not subscription.queue.empty():
                    subscription.queue.get_nowait()
                subscription.queue.put_nowait(
                    TaskSubscriptionOverflowError(event.event_seq - 1)
                )
