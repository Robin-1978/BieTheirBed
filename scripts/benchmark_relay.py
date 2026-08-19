from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import time
from pathlib import Path
from typing import Any

from knoa_platform.hub.relay import RelayBroker, RelayFrame

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "protocol" / "baseline" / "relay-performance-v1.json"


class _MeasuredWebSocket:
    def __init__(self) -> None:
        self.bytes_sent = 0
        self.closed = False

    async def send_json(self, message: dict[str, Any]) -> None:
        self.bytes_sent += len(
            json.dumps(message, separators=(",", ":")).encode("utf-8")
        )

    async def close(self, **_kwargs: Any) -> None:
        self.closed = True


def _frame(*, sequence: int, payload: bytes) -> RelayFrame:
    return RelayFrame(
        session_id="performance-session",
        stream_id=1,
        frame_type="data",
        sequence=sequence,
        ciphertext_length=len(payload),
        ciphertext=base64.urlsafe_b64encode(payload).decode("ascii").rstrip("="),
    )


async def measure(profile: dict[str, Any]) -> dict[str, float]:
    payload = b"r" * int(profile["payload_bytes"])
    frame = _frame(sequence=0, payload=payload)
    broker = RelayBroker()
    node_socket = _MeasuredWebSocket()
    client_socket = _MeasuredWebSocket()

    started = time.perf_counter()
    await broker.register_node("performance-node", node_socket)  # type: ignore[arg-type]
    await broker.register_client(
        "performance-session",
        "performance-node",
        client_socket,  # type: ignore[arg-type]
    )
    await broker.send_to_node("performance-node", frame)
    first_frame_ms = (time.perf_counter() - started) * 1000

    frame_count = int(profile["long_session_frames"])
    started = time.perf_counter()
    for sequence in range(1, frame_count + 1):
        frame.sequence = sequence
        await broker.send_to_client("performance-node", frame)
    long_session_seconds = time.perf_counter() - started
    throughput_mib_per_second = (
        frame_count * len(payload) / (1024 * 1024) / long_session_seconds
    )

    reconnect_samples: list[float] = []
    for _ in range(int(profile["reconnect_cycles"])):
        replacement = _MeasuredWebSocket()
        started = time.perf_counter()
        await broker.register_node(
            "performance-node",
            replacement,  # type: ignore[arg-type]
        )
        reconnect_samples.append((time.perf_counter() - started) * 1000)

    reconnect_samples.sort()
    p95_index = max(0, int(len(reconnect_samples) * 0.95) - 1)
    return {
        "first_frame_ms": round(first_frame_ms, 3),
        "throughput_mib_per_second": round(throughput_mib_per_second, 3),
        "long_session_seconds": round(long_session_seconds, 3),
        "reconnect_p95_ms": round(reconnect_samples[p95_index], 3),
        "reconnect_median_ms": round(statistics.median(reconnect_samples), 3),
    }


def _violations(
    measurements: dict[str, float], budgets: dict[str, float]
) -> list[str]:
    checks = {
        "first_frame_ms": ("max", budgets["first_frame_ms_max"]),
        "throughput_mib_per_second": (
            "min",
            budgets["throughput_mib_per_second_min"],
        ),
        "long_session_seconds": ("max", budgets["long_session_seconds_max"]),
        "reconnect_p95_ms": ("max", budgets["reconnect_p95_ms_max"]),
    }
    violations: list[str] = []
    for name, (direction, limit) in checks.items():
        actual = measurements[name]
        failed = actual > limit if direction == "max" else actual < limit
        if failed:
            violations.append(f"{name}={actual} violates {direction}={limit}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when a checked-in performance budget is exceeded",
    )
    args = parser.parse_args()
    profile = json.loads(BASELINE.read_text(encoding="utf-8"))
    measurements = asyncio.run(measure(profile))
    result = {
        "profile": profile["profile"],
        "measurements": measurements,
        "budgets": profile["budgets"],
    }
    print(json.dumps(result, sort_keys=True))
    if args.check:
        violations = _violations(measurements, profile["budgets"])
        if violations:
            raise RuntimeError("; ".join(violations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
