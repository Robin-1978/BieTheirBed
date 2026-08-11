"""Deterministic authorization-to-commit boundary for one tool call."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator
from pydantic import Field

from pc_assistant.agent_runtime.contracts import ContractModel, RuntimeScope
from pc_assistant.tools.base import (
    ToolCapability,
    ToolPolicy,
)
from pc_assistant.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ProposedToolCall(ContractModel):
    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolStepResult(ContractModel):
    call_id: str
    tool_name: str
    status: Literal["completed", "rejected", "failed", "not_executed"]
    code: str = ""
    message: str = ""
    output: Any = None


class ConfirmationPort(Protocol):
    async def confirm(
        self,
        scope: RuntimeScope,
        run_id: str,
        call: ProposedToolCall,
        reason: str,
    ) -> bool: ...


class ToolCommitPort(Protocol):
    async def begin(
        self,
        scope: RuntimeScope,
        task_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
    ) -> ToolStepResult | None: ...

    async def finish(
        self,
        scope: RuntimeScope,
        task_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
        result: ToolStepResult,
    ) -> None: ...


@dataclass(frozen=True)
class ToolStepContext:
    scope: RuntimeScope
    run_id: str
    client_request_id: str
    capabilities: frozenset[ToolCapability]
    cancellation: asyncio.Event
    confirmation: ConfirmationPort | None = None
    commit: ToolCommitPort | None = None


class ToolPolicyDeniedError(PermissionError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ToolOutcomeUnknownError(RuntimeError):
    """Tool returned, but its durable terminal checkpoint could not be stored."""


class ToolArgumentPolicy:
    """Normalize authority-sensitive arguments before the commit boundary."""

    def __init__(self, default_directory: str | Path) -> None:
        self._default_directory = Path(default_directory).expanduser().resolve()

    def normalize(
        self,
        capabilities: frozenset[ToolCapability],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        host_path_access = bool(
            capabilities
            & {
                ToolCapability.HOST_READ,
                ToolCapability.HOST_WRITE,
            }
        )
        if host_path_access and "path" in normalized:
            raw_path = str(normalized["path"]).strip()
            if not raw_path:
                raise ToolPolicyDeniedError(
                    "tool_invalid_arguments",
                    "Host path must not be empty",
                )
            candidate = Path(raw_path).expanduser()
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (self._default_directory / candidate).resolve()
            )
            normalized["path"] = str(resolved)
        return normalized


class ToolStep:
    """Validate, authorize, confirm, and commit exactly one proposed call."""

    def __init__(
        self,
        registry: ToolRegistry,
        argument_policy: ToolArgumentPolicy,
        *,
        prepare_execution: Callable[[str], None] | None = None,
    ) -> None:
        self._registry = registry
        self._argument_policy = argument_policy
        self._prepare_execution = prepare_execution

    @staticmethod
    def _result(
        call: ProposedToolCall,
        status: Literal["completed", "rejected", "failed", "not_executed"],
        *,
        tool_name: str | None = None,
        code: str = "",
        message: str = "",
        output: Any = None,
    ) -> ToolStepResult:
        return ToolStepResult(
            call_id=call.call_id,
            tool_name=tool_name or call.name,
            status=status,
            code=code,
            message=message,
            output=output,
        )

    async def execute(
        self,
        context: ToolStepContext,
        call: ProposedToolCall,
    ) -> ToolStepResult:
        tool_name = call.name
        tool = self._registry.get(tool_name)
        if tool is None:
            return self._result(
                call,
                "rejected",
                tool_name=tool_name,
                code="tool_not_found",
                message="Tool not found",
            )
        base_policy = tool.policy
        if not base_policy.configured:
            return self._result(
                call,
                "rejected",
                tool_name=tool_name,
                code="capability_denied",
                message="Tool policy is not configured",
            )
        validator = Draft202012Validator(tool.validation_schema())
        errors = sorted(
            validator.iter_errors(call.arguments),
            key=lambda error: repr(tuple(error.path)),
        )
        if errors:
            return self._result(
                call,
                "rejected",
                tool_name=tool_name,
                code="tool_invalid_arguments",
                message=errors[0].message,
            )
        policy = tool.policy_for(call.arguments)
        if not policy.configured or not policy.capabilities <= context.capabilities:
            return self._result(
                call,
                "rejected",
                tool_name=tool_name,
                code="capability_denied",
                message="Call-specific tool policy is not granted",
            )
        try:
            arguments = self._argument_policy.normalize(
                policy.capabilities,
                call.arguments,
            )
        except ToolPolicyDeniedError as exc:
            return self._result(
                call,
                "rejected",
                tool_name=tool_name,
                code=exc.code,
                message=str(exc),
            )

        if context.cancellation.is_set():
            return self._result(
                call,
                "not_executed",
                tool_name=tool_name,
                code="cancelled",
                message="Run cancelled before tool execution",
            )

        needs_confirmation = policy.requires_confirmation
        if needs_confirmation:
            if context.confirmation is None:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="confirmation_required",
                    message="Tool execution requires confirmation",
                )
            approved = await context.confirmation.confirm(
                context.scope,
                context.run_id,
                call.model_copy(update={"name": tool_name, "arguments": arguments}),
                f"{policy.effect.value}:{policy.risk.value}",
            )
            if not approved:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="confirmation_denied",
                    message="Tool execution was denied",
                )
        if context.cancellation.is_set():
            return self._result(
                call,
                "not_executed",
                tool_name=tool_name,
                code="cancelled",
                message="Run cancelled before tool commit",
            )

        if self._prepare_execution is not None:
            try:
                self._prepare_execution(tool_name)
            except Exception:  # noqa: BLE001 - isolate environment preparation failures
                return self._result(
                    call,
                    "failed",
                    tool_name=tool_name,
                    code="execution_environment_unavailable",
                    message="Tool execution environment is unavailable",
                )

        committed_call = call.model_copy(
            update={"name": tool_name, "arguments": arguments}
        )
        if context.commit is not None:
            prior = await context.commit.begin(
                context.scope,
                context.run_id,
                committed_call,
                policy,
            )
            if prior is not None:
                return prior

        try:
            output = await self._registry._commit(
                tool_name,
                scope=context.scope,
                **arguments,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Tool execution failed: %s", tool_name)
            result = self._result(
                call,
                "failed",
                tool_name=tool_name,
                code="tool_failed",
                message="Tool execution failed",
            )
        else:
            if isinstance(output, dict) and "error" in output:
                result = self._result(
                    call,
                    "failed",
                    tool_name=tool_name,
                    code="tool_failed",
                    message=str(output.get("error") or "Tool execution failed"),
                    output=output,
                )
            else:
                result = self._result(
                    call,
                    "completed",
                    tool_name=tool_name,
                    output=output,
                )
        if context.commit is not None:
            try:
                await context.commit.finish(
                    context.scope,
                    context.run_id,
                    committed_call,
                    policy,
                    result,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ToolOutcomeUnknownError(
                    "Tool outcome could not be durably checkpointed"
                ) from exc
        return result
