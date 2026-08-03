from __future__ import annotations

from pc_assistant.context.evidence import EvidencePolicy


class TestEvidencePolicy:
    def test_greetings_no_evidence(self):
        policy = EvidencePolicy()
        assert not policy.requires_evidence("hi")
        assert not policy.requires_evidence("Hello there!")
        assert not policy.requires_evidence("你好")

    def test_numeric_requires_evidence(self):
        policy = EvidencePolicy()
        assert policy.requires_evidence("现在股票价格是多少？")
        assert policy.requires_evidence("report the 42.5 figure")

    def test_stateful_queries_require_evidence(self):
        policy = EvidencePolicy()
        assert policy.requires_evidence("what time is it?")
        assert policy.requires_evidence("check weather")
        assert policy.requires_evidence("当前进程占用")

    def test_disabled(self):
        policy = EvidencePolicy(enabled=False)
        assert not policy.requires_evidence("what time is it?")

    def test_empty(self):
        policy = EvidencePolicy()
        assert not policy.requires_evidence("")

    def test_build_instruction(self):
        policy = EvidencePolicy()
        instruction = policy.build_instruction()
        assert "tool results" in instruction or "工具" in instruction

    def test_satisfied(self):
        policy = EvidencePolicy()
        assert policy.satisfied(1)
        assert not policy.satisfied(0)
