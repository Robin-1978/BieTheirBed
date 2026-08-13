"""Constrained Agent review for an already-authorized Approval."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from knoa_agent_contracts import (
    AssistantDelta,
    CreateRuntimeSession,
    McpEndpointGrant,
    RuntimeTurnRequest,
    TextPart,
    TurnFinished,
)
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agents.manager import AgentManager
from knoa_platform.capabilities.gateway import CapabilityGateway


class ApprovalReviewMode(str, Enum):
    OFF = "off"
    SUGGEST = "suggest"
    AUTO = "auto"


class ApprovalReviewDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"


class ApprovalReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any] = Field(default_factory=dict)
    effect: str = Field(min_length=1, max_length=64)
    risk: str = Field(min_length=1, max_length=32)
    reason: str = Field(default="", max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ApprovalReviewDecision
    reason: str = Field(default="", max_length=2000)
    rule_ids: tuple[str, ...] = Field(default=(), max_length=32)
    reviewer_id: str = "reviewer_agent"
    model: str = ""


class ApprovalReviewer(Protocol):
    async def review(self, request: ApprovalReviewRequest) -> ApprovalReviewResult: ...


class NoopApprovalReviewer:
    async def review(self, request: ApprovalReviewRequest) -> ApprovalReviewResult:
        del request
        return ApprovalReviewResult(decision=ApprovalReviewDecision.ESCALATE)


class KnoaReviewerAgent:
    """Invoke a system-only Agent through the normal Agent Runtime SPI."""

    def __init__(
        self,
        agents: AgentManager,
        gateway: CapabilityGateway,
        *,
        agent_id: str = "reviewer_agent",
        model: str = "",
        timeout_seconds: float = 15.0,
    ) -> None:
        self._agents = agents
        self._gateway = gateway
        self._agent_id = agent_id
        self._model = model
        self._timeout = timeout_seconds

    async def review(self, request: ApprovalReviewRequest) -> ApprovalReviewResult:
        try:
            return await asyncio.wait_for(self._review(request), self._timeout)
        except Exception as exc:  # noqa: BLE001 - reviewer fails closed to human
            return ApprovalReviewResult(
                decision=ApprovalReviewDecision.ESCALATE,
                reason=f"Reviewer unavailable or returned invalid output: {type(exc).__name__}",
                reviewer_id=self._agent_id,
                model=self._model,
            )

    async def _review(self, request: ApprovalReviewRequest) -> ApprovalReviewResult:
        operation = f"review:{uuid.uuid4().hex}"
        scope = RuntimeScope(
            principal_id=request.principal_id,
            session_handle=f"review-{uuid.uuid4().hex}",
        )
        cancellation = asyncio.Event()
        async with self._agents.lease_system(self._agent_id) as runtime:
            session = await runtime.create_session(
                CreateRuntimeSession(operation_id=operation, binding_epoch=1)
            )
            grant = await self._gateway.grants.issue(
                scope=scope,
                run_id=request.run_id,
                client_request_id=operation,
                capabilities=frozenset(),
                cancellation=cancellation,
                confirmation=None,
                tool_commit=None,
                binding_epoch=session.binding_epoch,
                ttl_seconds=max(30.0, self._timeout + 5.0),
                allow_tools=False,
            )
            endpoint = McpEndpointGrant(
                server_id="knoa-platform-capabilities",
                transport="in_memory",
                endpoint="memory://knoa-platform-capabilities",
                authorization=grant.token,
                expires_at=grant.expires_at,
                scope_digest=grant.scope_digest,
                binding_epoch=grant.binding_epoch,
            )
            content: list[str] = []
            terminal: TurnFinished | None = None
            try:
                turn = await runtime.start_turn(
                    RuntimeTurnRequest(
                        session=session,
                        operation_id=operation,
                        input=(
                            TextPart(
                                text=json.dumps(
                                    request.model_dump(mode="json"),
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                            ),
                        ),
                        mcp=endpoint,
                        deadline=time.time() + self._timeout,
                    )
                )
                async for event in turn.events:
                    if isinstance(event, AssistantDelta):
                        content.append(event.content)
                    elif isinstance(event, TurnFinished):
                        terminal = event
            finally:
                await self._gateway.grants.revoke(grant.token)
                await runtime.delete_session(session)
            if terminal is None or terminal.status != "completed":
                raise RuntimeError("reviewer turn did not complete")
            payload = self._parse_json("".join(content))
            result = ApprovalReviewResult.model_validate(payload)
            return result.model_copy(
                update={"reviewer_id": self._agent_id, "model": self._model}
            )

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        candidate = content.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL | re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1)
        payload = json.loads(candidate)
        if not isinstance(payload, dict):
            raise TypeError("reviewer output must be an object")
        return payload
