from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from knoa_agent_contracts import (
    AgentDescriptor,
    AssistantDelta,
    RuntimeLimits,
    RuntimeSession,
    RuntimeTurn,
    TurnFinished,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.approvals import (
    ApprovalReviewDecision,
    ApprovalReviewMode,
    ApprovalReviewResult,
    KnoaReviewerAgent,
)
from knoa_platform.capabilities import CapabilityGrantRegistry
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


class CapturingRuntime:
    def __init__(self) -> None:
        self.requests = []
        self._descriptor = AgentDescriptor(
            agent_id="reviewer_agent",
            display_name="Reviewer",
            implementation_version="1",
            limits=RuntimeLimits(max_concurrent_turns=1),
        )

    @property
    def descriptor(self):
        return self._descriptor

    async def create_session(self, request):
        return RuntimeSession(
            agent_id="reviewer_agent",
            runtime_session_ref="review-session",
            runtime_protocol_version="1.0",
            binding_epoch=request.binding_epoch,
        )

    async def start_turn(self, request):
        self.requests.append(request)

        async def events():
            yield AssistantDelta(
                runtime_session_ref=request.session.runtime_session_ref,
                runtime_turn_ref="review-turn",
                occurred_at=1.0,
                content='{"decision":"escalate","reason":"human required","rule_ids":[]}',
            )
            yield TurnFinished(
                runtime_session_ref=request.session.runtime_session_ref,
                runtime_turn_ref="review-turn",
                occurred_at=2.0,
                status="completed",
            )

        return RuntimeTurn(runtime_turn_ref="review-turn", events=events())

    async def delete_session(self, session):
        del session


class ReviewerGateway:
    def __init__(self) -> None:
        self.grants = CapabilityGrantRegistry()


class ReviewerManager:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    @asynccontextmanager
    async def lease_system(self, agent_id):
        assert agent_id == "reviewer_agent"
        yield self.runtime


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


@pytest.mark.asyncio
async def test_reviewer_agent_never_receives_user_memory_context() -> None:
    runtime = CapturingRuntime()
    reviewer = KnoaReviewerAgent(
        ReviewerManager(runtime),
        ReviewerGateway(),
    )

    result = await reviewer.review(
        type(
            "Request",
            (),
            {
                "principal_id": "principal-a",
                "run_id": "run-a",
                "model_dump": lambda self, **_kwargs: {
                    "principal_id": "principal-a",
                    "run_id": "run-a",
                    "tool_name": "write_file",
                },
            },
        )()
    )

    assert result.decision is ApprovalReviewDecision.ESCALATE
    assert runtime.requests[0].context.core_memory == ()
    assert runtime.requests[0].context.relevant_memory == ()
    assert runtime.requests[0].context.episodic_memory == ()
    assert runtime.requests[0].context.skill_instructions == ""


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
