from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.approvals import (
    ApprovalReviewDecision,
    ApprovalReviewMode,
    ApprovalReviewResult,
    KnoaReviewerAgent,
)
from knoa_platform.tasks import TaskEventHub, TaskRepository
from knoa_platform.tasks.approval import DurableApprovalService


class Reviewer:
    def __init__(self, decision: ApprovalReviewDecision) -> None:
        self.decision = decision
        self.requests = []

    async def review(self, request):
        self.requests.append(request)
        return ApprovalReviewResult(
            decision=self.decision,
            reason="action matches the explicit request",
            rule_ids=("scope.explicit",),
            model="qwen3.5-4b",
        )


def test_reviewer_json_contract_accepts_plain_and_fenced_json():
    plain = KnoaReviewerAgent._parse_json(
        '{"decision":"approve","reason":"ok","rule_ids":[]}'
    )
    fenced = KnoaReviewerAgent._parse_json(
        '```json\n{"decision":"escalate","reason":"missing fact","rule_ids":[]}\n```'
    )

    assert plain["decision"] == "approve"
    assert fenced["decision"] == "escalate"


def test_reviewer_json_contract_rejects_non_object():
    with pytest.raises(TypeError, match="must be an object"):
        KnoaReviewerAgent._parse_json("[]")


def _running_task(tmp_path: Path):
    database = tmp_path / "runtime.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = TaskRepository(
        database,
        task_id_factory=lambda: "task-a",
        approval_id_factory=lambda: "approval-a",
    )
    task, _ = repository.create(
        scope,
        client_request_id="request-a",
        goal="Retry the failed build job",
        attachments=(),
        tools_enabled=True,
        priority=0,
    )
    repository.claim_next("worker", lease_seconds=60)
    return repository, scope, task


@pytest.mark.asyncio
async def test_suggest_records_reviewer_advice_and_still_waits_for_human(tmp_path: Path):
    repository, scope, task = _running_task(tmp_path)
    reviewer = Reviewer(ApprovalReviewDecision.APPROVE)
    service = DurableApprovalService(
        repository,
        TaskEventHub(),
        reviewer=reviewer,
        review_mode=ApprovalReviewMode.SUGGEST,
    )
    pending = asyncio.create_task(
        service.confirm(
            scope,
            task.task_id,
            ProposedToolCall(call_id="call-a", name="gitlab.retry_job", arguments={"job_id": 1}),
            "external_side_effect:medium",
        )
    )
    await asyncio.sleep(0.05)

    assert not pending.done()
    approval = repository.get_approval(scope.principal_id, "approval-a")
    assert "reviewer[reviewer_agent/qwen3.5-4b]=approve" in approval.reason
    assert reviewer.requests[0].context["user_intent"] == "Retry the failed build job"
    await service.resolve(scope.principal_id, approval.approval_id, approved=False)
    assert await pending is False


@pytest.mark.asyncio
async def test_auto_review_approves_medium_risk_but_never_high_risk(tmp_path: Path):
    repository, scope, task = _running_task(tmp_path)
    reviewer = Reviewer(ApprovalReviewDecision.APPROVE)
    service = DurableApprovalService(
        repository,
        TaskEventHub(),
        reviewer=reviewer,
        review_mode=ApprovalReviewMode.AUTO,
        auto_max_risk="medium",
    )

    assert await service.confirm(
        scope,
        task.task_id,
        ProposedToolCall(call_id="call-a", name="gitlab.retry_job", arguments={"job_id": 1}),
        "external_side_effect:medium",
    ) is True
    approval = repository.get_approval(scope.principal_id, "approval-a")
    assert approval.resolved_by == "approval_reviewer:reviewer_agent"


@pytest.mark.asyncio
async def test_auto_review_never_auto_resolves_high_risk(tmp_path: Path):
    repository, scope, task = _running_task(tmp_path)
    reviewer = Reviewer(ApprovalReviewDecision.APPROVE)
    service = DurableApprovalService(
        repository,
        TaskEventHub(),
        reviewer=reviewer,
        review_mode=ApprovalReviewMode.AUTO,
        auto_max_risk="medium",
    )
    pending = asyncio.create_task(
        service.confirm(
            scope,
            task.task_id,
            ProposedToolCall(call_id="call-a", name="run_command", arguments={"cmd": "echo ok"}),
            "local_write:high",
        )
    )
    await asyncio.sleep(0.05)

    assert not pending.done()
    await service.resolve(scope.principal_id, "approval-a", approved=False)
    assert await pending is False
