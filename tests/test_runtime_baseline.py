from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_baseline_covers_writer_boundaries() -> None:
    baseline = json.loads(
        (ROOT / "protocol" / "baseline" / "runtime-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["schema_version"] == 1
    assert set(baseline["sqlite_schemas"]) == {
        "node_authority",
        "node_gateway",
        "self_hosted_hub",
        "hosted_control",
    }
    assert set(baseline["relay_transcripts"]) == {
        "app_session",
        "app_pairing",
        "node_resource",
    }
    assert all(
        len(digest) == 64 for digest in baseline["contracts"].values()
    )


def test_node_authority_baseline_contains_work_and_configuration_tables() -> None:
    baseline = json.loads(
        (ROOT / "protocol" / "baseline" / "runtime-v1.json").read_text(
            encoding="utf-8"
        )
    )
    tables = {
        item["name"]
        for item in baseline["sqlite_schemas"]["node_authority"]
        if item["type"] == "table"
    }
    assert {
        "config_revisions",
        "runtime_sessions",
        "conversation_sessions",
        "tasks",
        "task_executions",
        "runtime_tasks",
        "runtime_task_approvals",
    }.issubset(tables)
