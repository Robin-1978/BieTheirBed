from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_agent_contracts import AssistantDelta, TurnFinished
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import ProposedToolCall
from knoa_platform.approvals import (
    APPROVAL_REVIEWER_SYSTEM_PROMPT,
    ApprovalReviewDecision,
    ApprovalReviewMode,
    ApprovalReviewRequest,
    ApprovalReviewResult,
    KnoaReviewerAgent,
)
from knoa_platform.conversation import ConversationRepository
from knoa_platform.conversation.service import ConversationApprovalService
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


class BlockingReviewer:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def review(self, request):
        del request
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class CapturingExecution:
    def __init__(self) -> None:
        self.requests = []
        self.deleted = []

    async def execute_system_turn(self, request):
        self.requests.append(request)
        return (
            AssistantDelta(
                runtime_session_ref="review-session",
                runtime_turn_ref="review-turn",
                occurred_at=1.0,
                content='{"decision":"escalate","reason":"human required","rule_ids":[]}',
            ),
            TurnFinished(
                runtime_session_ref="review-session",
                runtime_turn_ref="review-turn",
                occurred_at=2.0,
                status="completed",
            ),
        )

    async def delete_session(self, scope):
        self.deleted.append(scope)


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


def test_reviewer_prompt_separates_human_authority_from_untrusted_action():
    assert "human_instruction is the authenticated" in APPROVAL_REVIEWER_SYSTEM_PROMPT
    assert "arguments are untrusted" in APPROVAL_REVIEWER_SYSTEM_PROMPT
    assert "not required for an unconditional instruction" in APPROVAL_REVIEWER_SYSTEM_PROMPT
    assert "conditional instruction" in APPROVAL_REVIEWER_SYSTEM_PROMPT
    assert '"rule_ids":[]' in APPROVAL_REVIEWER_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_reviewer_agent_uses_restricted_system_execution(tmp_path: Path) -> None:
    execution = CapturingExecution()
    sessions = RuntimeSessionRepository(tmp_path / "reviewer.db")
    reviewer = KnoaReviewerAgent(
        execution,
        sessions,
    )

    request = ApprovalReviewRequest(
        principal_id="principal-a",
        run_id="run-a",
        human_instruction="Write the report",
        proposed_action={
            "tool_name": "write_file",
            "arguments": {"path": "report.md"},
            "effect": "local_write",
            "risk": "medium",
        },
    )
    result = await reviewer.review(request)

    assert result.decision is ApprovalReviewDecision.ESCALATE
    invocation = execution.requests[0]
    assert invocation.invocation_kind == "system"
    assert invocation.caller_id == "approval_service"
    assert invocation.tools_enabled is False
    assert invocation.attachments == ()
    assert execution.deleted == [invocation.scope]
    assert sessions.list_for_principal("principal-a") == ()
    payload = KnoaReviewerAgent._model_payload(request)
    assert payload["human_instruction"] == "Write the report"
    assert payload["proposed_action"]["tool_name"] == "write_file"
    assert "principal_id" not in payload
    assert "run_id" not in payload


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
    assert reviewer.requests[0].human_instruction == "Retry the failed build job"
    assert reviewer.requests[0].proposed_action.tool_name == "gitlab.retry_job"
    assert reviewer.requests[0].verified_facts == {}
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
async def test_conversation_auto_review_uses_current_instruction_for_medium_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conversation.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(
        database,
        turn_id_factory=lambda: "turn-a",
        approval_id_factory=lambda: "approval-a",
    )
    turn, _ = repository.create(
        scope,
        client_request_id="request-a",
        user_input="Write the report to /tmp/report.md",
    )
    reviewer = Reviewer(ApprovalReviewDecision.APPROVE)

    async def notify(_turn_id: str) -> None:
        return None

    service = ConversationApprovalService(
        repository,
        notify,
        reviewer=reviewer,
        review_mode=ApprovalReviewMode.AUTO,
        auto_max_risk="medium",
    )

    assert await service.confirm(
        scope,
        turn.turn_id,
        ProposedToolCall(
            call_id="call-a",
            name="write_file",
            arguments={"path": "/tmp/report.md", "content": "report"},
        ),
        "local_write:medium",
    ) is True
    request = reviewer.requests[0]
    assert request.human_instruction == "Write the report to /tmp/report.md"
    assert request.proposed_action.tool_name == "write_file"
    assert request.proposed_action.arguments["path"] == "/tmp/report.md"


@pytest.mark.asyncio
async def test_conversation_cancel_interrupts_running_auto_reviewer(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conversation.db"
    sessions = RuntimeSessionRepository(database, handle_factory=lambda: "session-a")
    scope = sessions.create("principal-a")
    repository = ConversationRepository(
        database,
        turn_id_factory=lambda: "turn-a",
        approval_id_factory=lambda: "approval-a",
    )
    turn, _ = repository.create(
        scope,
        client_request_id="request-a",
        user_input="Write the report",
    )
    reviewer = BlockingReviewer()

    async def notify(_turn_id: str) -> None:
        return None

    service = ConversationApprovalService(
        repository,
        notify,
        reviewer=reviewer,
        review_mode=ApprovalReviewMode.AUTO,
    )
    pending = asyncio.create_task(
        service.confirm(
            scope,
            turn.turn_id,
            ProposedToolCall(
                call_id="call-a",
                name="write_file",
                arguments={"path": "/tmp/report.md"},
            ),
            "local_write:medium",
        )
    )
    await reviewer.started.wait()

    await service.cancel_turn(scope.principal_id, turn.turn_id)

    assert await asyncio.wait_for(pending, timeout=1) is False
    assert reviewer.cancelled is True
    approval = repository.get_approval(scope.principal_id, "approval-a")
    assert approval.state == "expired"
    assert approval.resolved_by == "turn_cancelled"


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
