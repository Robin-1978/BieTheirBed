"""Small transport health model shared by Console and diagnostics.

The model intentionally records discovery, verification and request stages
separately.  A successful mDNS announcement is not the same as an App finding
the Node, and a successful P2P answer is not the same as a usable request
channel.  Keeping those stages explicit makes Windows/LAN failures
actionable while preserving automatic mDNS > P2P > Relay selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

TransportName = Literal["mdns", "p2p", "relay"]
Stage = Literal["discovery", "verification", "request"]


@dataclass(slots=True)
class TransportHealth:
    preferred_order: tuple[TransportName, ...] = ("mdns", "p2p", "relay")
    active: TransportName | None = None
    last_switch_reason: str = ""
    switch_count: int = 0
    discovery_success: dict[TransportName, int] = field(default_factory=lambda: {"mdns": 0, "p2p": 0, "relay": 0})
    verification_success: dict[TransportName, int] = field(default_factory=lambda: {"mdns": 0, "p2p": 0, "relay": 0})
    request_success: dict[TransportName, int] = field(default_factory=lambda: {"mdns": 0, "p2p": 0, "relay": 0})
    last_request_latency_ms: dict[TransportName, float] = field(default_factory=dict)
    request_latency_samples: dict[TransportName, int] = field(default_factory=dict)
    request_latency_total_ms: dict[TransportName, float] = field(default_factory=dict)
    last_error: dict[TransportName, str] = field(default_factory=dict)
    last_changed_at: float = field(default_factory=monotonic)
    _observed: dict[str, bool] = field(default_factory=dict, repr=False)

    def record(self, transport: TransportName, stage: Stage, *, ok: bool, error: str = "") -> None:
        target = {
            "discovery": self.discovery_success,
            "verification": self.verification_success,
            "request": self.request_success,
        }[stage]
        if ok:
            target[transport] = target.get(transport, 0) + 1
        elif error:
            self.last_error[transport] = " ".join(error.split())[:240]

    def observe(self, transport: TransportName, stage: Stage, *, ok: bool, error: str = "") -> None:
        """Record a live status observation without inflating poll-based metrics.

        Console status is polled repeatedly.  A healthy state observed ten
        times is still one discovery/verification success; a later recovery
        after a failed state counts as a new success.
        """
        key = f"{transport}:{stage}"
        previous = self._observed.get(key)
        self._observed[key] = ok
        if ok and previous is not True:
            self.record(transport, stage, ok=True)
        elif not ok and error:
            self.record(transport, stage, ok=False, error=error)

    def activate(self, transport: TransportName, *, reason: str = "") -> None:
        if self.active != transport:
            self.switch_count += 1
            self.last_changed_at = monotonic()
        self.active = transport
        self.last_switch_reason = " ".join(reason.split())[:240]

    def record_request_latency(self, transport: TransportName, elapsed_ms: float) -> None:
        elapsed = max(0.0, float(elapsed_ms))
        self.last_request_latency_ms[transport] = round(elapsed, 2)
        self.request_latency_samples[transport] = self.request_latency_samples.get(transport, 0) + 1
        self.request_latency_total_ms[transport] = self.request_latency_total_ms.get(transport, 0.0) + elapsed

    def snapshot(self) -> dict[str, object]:
        return {
            "preferred_order": list(self.preferred_order),
            "active": self.active,
            "last_switch_reason": self.last_switch_reason,
            "switch_count": self.switch_count,
            "discovery_success": dict(self.discovery_success),
            "verification_success": dict(self.verification_success),
            "request_success": dict(self.request_success),
            "last_request_latency_ms": dict(self.last_request_latency_ms),
            "average_request_latency_ms": {
                transport: round(self.request_latency_total_ms[transport] / self.request_latency_samples[transport], 2)
                for transport in self.request_latency_samples
                if self.request_latency_samples[transport] > 0
            },
            "last_error": dict(self.last_error),
            "last_changed_age_seconds": max(0.0, monotonic() - self.last_changed_at),
        }


__all__ = ["Stage", "TransportHealth", "TransportName"]
