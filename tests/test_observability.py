from __future__ import annotations

import stat

from pc_assistant.observability.trace import (
    JsonlRecorder,
    LLMTraceRecorder,
    TurnRecorder,
)


class TestJsonlRecorder:
    def test_records_to_file(self, tmp_path):
        path = str(tmp_path / "traces.jsonl")
        rec = JsonlRecorder(path=path, enabled=True)
        rec.record({"kind": "x", "value": 1})
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 1
        assert stat.S_IMODE((tmp_path / "traces.jsonl").stat().st_mode) == 0o600

    def test_ring_buffer(self, tmp_path):
        rec = JsonlRecorder(path=str(tmp_path / "t.jsonl"), enabled=True, ring=3)
        for i in range(5):
            rec.record({"i": i})
        recent = rec.recent()
        assert [r["i"] for r in recent] == [2, 3, 4]

    def test_disabled_noop(self, tmp_path):
        rec = JsonlRecorder(path=str(tmp_path / "t.jsonl"), enabled=False)
        rec.record({"x": 1})
        assert rec.recent() == []


class TestLLMTraceRecorder:
    def test_record_call(self, tmp_path):
        rec = LLMTraceRecorder(path=str(tmp_path / "llm.jsonl"), enabled=True)
        rec.record_call(
            principal_id="user-a", session_id="s1", run_id="run-a",
            client_request_id="request-a", model="m", iteration=1,
            prompt_tokens=10, completion_tokens=5,
            latency_ms=100.0, ttft_ms=20.0,
            finish_reason="stop", tool_calls=1,
            failover_used=True,
        )
        entry = rec.recent(1)[0]
        assert entry["kind"] == "llm_call"
        assert entry["total_tokens"] == 15
        assert entry["session_hash"] != "s1"
        assert entry["run_hash"] != "run-a"
        assert entry["client_request_hash"] != "request-a"
        assert "session_id" not in entry
        assert "run_id" not in entry
        assert "client_request_id" not in entry
        assert entry["principal_hash"] != "user-a"
        assert entry["failover_used"] is True


class TestTurnRecorder:
    def test_record_turn(self, tmp_path):
        rec = TurnRecorder(path=str(tmp_path / "turns.jsonl"), enabled=True)
        rec.record_turn(
            principal_id="user-a", session_id="s1", run_id="run-a",
            client_request_id="request-a", user_input="hi",
            outcome="answer", iterations=1, tool_calls=0,
            evidence_required=True, evidence_satisfied=False,
        )
        entry = rec.recent(1)[0]
        assert entry["kind"] == "turn"
        assert entry["outcome"] == "answer"
        assert entry["evidence_required"] is True
        assert entry["input_chars"] == 2
        assert "user_input" not in entry
        assert "session_id" not in entry
        assert "run_id" not in entry
        assert "client_request_id" not in entry
