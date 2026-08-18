"""Portable graceful-shutdown coordination for foreground service hosts."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path


async def wait_for_shutdown(stop_request: Path) -> None:
    stop_request.parent.mkdir(parents=True, exist_ok=True)
    stop_request.unlink(missing_ok=True)
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    fallback_handlers: list[tuple[signal.Signals, object]] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, event.set)
        except (NotImplementedError, RuntimeError):
            try:
                previous = signal.signal(
                    sig,
                    lambda _number, _frame: loop.call_soon_threadsafe(event.set),
                )
                fallback_handlers.append((sig, previous))
            except (OSError, ValueError):
                pass

    async def poll_stop_request() -> None:
        while not event.is_set():
            if stop_request.exists():
                stop_request.unlink(missing_ok=True)
                event.set()
                return
            await asyncio.sleep(0.5)

    poller = asyncio.create_task(poll_stop_request(), name="knoa-stop-request")
    try:
        await event.wait()
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        for sig, previous in fallback_handlers:
            try:
                signal.signal(sig, previous)
            except (OSError, ValueError):
                pass


__all__ = ["wait_for_shutdown"]
