from __future__ import annotations

import stat

from knoa_platform.observability.trace import (
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
            prompt_tokens_estimated=12,
            prompt_tokens_source="provider",
            completion_tokens_source="provider",
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
        assert entry["prompt_tokens_estimated"] == 12
        assert entry["prompt_tokens_source"] == "provider"
        assert entry["completion_tokens_source"] == "provider"

    def test_session_totals_include_persisted_calls(self, tmp_path):
        path = str(tmp_path / "llm.jsonl")
        rec = LLMTraceRecorder(path=path, enabled=True)
        for prompt, completion, cached in ((10, 5, 2), (20, 7, 3)):
            rec.record_call(
                principal_id="user-a",
                session_id="s1",
                run_id=f"run-{prompt}",
                client_request_id=f"request-{prompt}",
                model="model-a",
                iteration=1,
                prompt_tokens=prompt,
                completion_tokens=completion,
                cached_tokens=cached,
                prompt_tokens_estimated=prompt + 1,
                prompt_tokens_source="provider",
                completion_tokens_source="provider",
            )
        rec.record_call(
            principal_id="user-a",
            session_id="other",
            run_id="other-run",
            client_request_id="other-request",
            model="model-b",
            iteration=1,
            prompt_tokens=999,
        )

        totals = LLMTraceRecorder(path=path, enabled=True).session_totals(
            "user-a",
            "s1",
        )

        assert totals == {
            "prompt_tokens": 30,
            "completion_tokens": 12,
            "cached_tokens": 5,
            "prompt_tokens_estimated": 32,
            "total_tokens": 42,
            "model_calls": 2,
            "usage_source": "provider",
            "model": "model-a",
        }


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

    def test_session_totals_include_persisted_turns(self, tmp_path):
        path = str(tmp_path / "turns.jsonl")
        rec = TurnRecorder(path=path, enabled=True)
        for run_id, iterations, tool_calls in (("a", 2, 1), ("b", 3, 4)):
            rec.record_turn(
                principal_id="user-a",
                session_id="s1",
                run_id=run_id,
                client_request_id=f"request-{run_id}",
                user_input="hello",
                outcome="completed",
                iterations=iterations,
                tool_calls=tool_calls,
            )

        totals = TurnRecorder(path=path, enabled=True).session_totals(
            "user-a",
            "s1",
        )

        assert totals == {
            "turns": 2,
            "iterations": 5,
            "tool_calls": 5,
            "last_outcome": "completed",
        }
