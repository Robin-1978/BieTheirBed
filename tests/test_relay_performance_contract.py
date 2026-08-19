from __future__ import annotations

import json
from pathlib import Path

from scripts.benchmark_relay import _violations

ROOT = Path(__file__).resolve().parents[1]


def test_relay_performance_budget_has_all_phase_zero_dimensions() -> None:
    baseline = json.loads(
        (ROOT / "protocol" / "baseline" / "relay-performance-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["schema_version"] == 1
    assert set(baseline["budgets"]) == {
        "first_frame_ms_max",
        "throughput_mib_per_second_min",
        "long_session_seconds_max",
        "reconnect_p95_ms_max",
    }


def test_relay_performance_budget_detects_regressions() -> None:
    budgets = {
        "first_frame_ms_max": 100.0,
        "throughput_mib_per_second_min": 5.0,
        "long_session_seconds_max": 15.0,
        "reconnect_p95_ms_max": 100.0,
    }
    passing = {
        "first_frame_ms": 1.0,
        "throughput_mib_per_second": 100.0,
        "long_session_seconds": 1.0,
        "reconnect_p95_ms": 1.0,
    }
    assert _violations(passing, budgets) == []
    passing["throughput_mib_per_second"] = 1.0
    assert _violations(passing, budgets) == [
        "throughput_mib_per_second=1.0 violates min=5.0"
    ]
