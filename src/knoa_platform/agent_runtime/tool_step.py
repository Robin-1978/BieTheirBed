"""Deterministic authorization-to-commit boundary for one tool call."""
from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator
from pydantic import Field

from knoa_platform.agent_runtime.contracts import ContractModel, RuntimeScope
from knoa_platform.context.scope import (
    MemoryScope,
    reset_memory_scope,
    set_memory_scope,
)
from knoa_platform.tools.base import (
    ToolCapability,
    ToolPolicy,
)
from knoa_platform.tools.registry import ToolRegistry

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
    interaction: Any = None


_CURRENT_TOOL_STEP_CONTEXT: ContextVar[ToolStepContext | None] = ContextVar(
    "knoa_current_tool_step_context",
    default=None,
)


def current_tool_step_context() -> ToolStepContext | None:
    return _CURRENT_TOOL_STEP_CONTEXT.get()


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
        approved_schema = copy.deepcopy(tool.validation_schema())
        validator = Draft202012Validator(approved_schema)
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
            if context.cancellation.is_set():
                return self._result(
                    call,
                    "not_executed",
                    tool_name=tool_name,
                    code="cancelled",
                    message="Run cancelled while waiting for confirmation",
                )
            if not approved:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="confirmation_denied",
                    message="Tool execution was denied",
                )
            current_tool = self._registry.get(tool_name)
            if current_tool is not tool:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool definition changed after confirmation",
                )
            try:
                current_schema = current_tool.validation_schema()
            except Exception:  # noqa: BLE001 - changed Tool definitions fail closed
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool schema changed after confirmation",
                )
            if current_schema != approved_schema:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool schema changed after confirmation",
                )
            current_errors = sorted(
                Draft202012Validator(current_schema).iter_errors(call.arguments),
                key=lambda error: repr(tuple(error.path)),
            )
            if current_errors:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool arguments are no longer valid after confirmation",
                )
            current_policy = current_tool.policy_for(call.arguments)
            if (
                current_policy != policy
                or not current_policy.configured
                or not current_policy.capabilities <= context.capabilities
            ):
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool policy changed after confirmation",
                )
            try:
                current_arguments = self._argument_policy.normalize(
                    current_policy.capabilities,
                    call.arguments,
                )
            except ToolPolicyDeniedError:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool arguments changed after confirmation",
                )
            if current_arguments != arguments:
                return self._result(
                    call,
                    "rejected",
                    tool_name=tool_name,
                    code="approval_stale",
                    message="Tool arguments changed after confirmation",
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

        scope_token = set_memory_scope(
            MemoryScope(
                principal_id=context.scope.principal_id,
                session_id=context.scope.session_handle,
            )
        )
        tool_context_token: Token[ToolStepContext | None] = (
            _CURRENT_TOOL_STEP_CONTEXT.set(context)
        )
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
        finally:
            _CURRENT_TOOL_STEP_CONTEXT.reset(tool_context_token)
            reset_memory_scope(scope_token)
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
