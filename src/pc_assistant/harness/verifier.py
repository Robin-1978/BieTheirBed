"""Deterministic Verifier — the core of the Stochastic-Deterministic Boundary.

Sits between the LLM (proposer) and tool execution (commit).  For every
proposed tool call it runs: schema validation → safety policy → confirmation
gate, and returns a typed ``Verdict`` (accept / reject with refusal code).
"""
from __future__ import annotations

from typing import Any

from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.confirm import ConfirmFn, resolve_confirm
from pc_assistant.harness.refusal import RefusalCode, Verdict
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.tools.registry import ToolRegistry


class Verifier:
    """Deterministic gate between LLM proposals and tool execution."""

    def __init__(
        self,
        safety: SafetyChecker,
        registry: ToolRegistry,
        audit: AuditLogger,
        confirm_callback: ConfirmFn | None = None,
    ) -> None:
        self._safety = safety
        self._registry = registry
        self._audit = audit
        self._confirm_callback = confirm_callback

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        confirm_callback: ConfirmFn | None = None,
    ) -> Verdict:
        cb = confirm_callback or self._confirm_callback

        if tool_name not in self._registry:
            self._audit.log(
                action="tool_call_blocked",
                tool=tool_name,
                parameters=arguments,
                allowed=False,
                reason="Tool not found",
            )
            return Verdict.reject(
                RefusalCode.TOOL_NOT_FOUND,
                f"Tool '{tool_name}' not found in registry.",
                retry_hint="Use describe_tool to list available tools.",
            )

        safety_result = self._safety.check_tool_call(tool_name, arguments)
        need_confirm, confirm_reason = self._safety.needs_confirmation(tool_name, arguments)

        if not safety_result:
            if cb is not None:
                confirmed = await resolve_confirm(cb, tool_name, arguments)
                if confirmed:
                    self._audit.log(
                        action="tool_call_confirmed",
                        tool=tool_name,
                        parameters=arguments,
                        allowed=True,
                        reason=f"User confirmed override of: {safety_result.reason}",
                    )
                    return Verdict.accept()
                self._audit.log(
                    action="tool_call_blocked",
                    tool=tool_name,
                    parameters=arguments,
                    allowed=False,
                    reason=f"User denied: {safety_result.reason}",
                )
                return Verdict.reject(
                    RefusalCode.CONFIRMATION_DENIED,
                    safety_result.reason,
                    retry_hint="User denied the operation. Ask before retrying.",
                )
            self._audit.log(
                action="tool_call_blocked",
                tool=tool_name,
                parameters=arguments,
                allowed=False,
                reason=safety_result.reason,
            )
            code = self._classify_safety_reason(safety_result.reason)
            return Verdict.reject(
                code,
                safety_result.reason,
                retry_hint="Rephrase the command to avoid dangerous patterns.",
            )

        if need_confirm:
            if cb is not None:
                confirmed = await resolve_confirm(cb, tool_name, arguments)
                if not confirmed:
                    self._audit.log(
                        action="tool_call_blocked",
                        tool=tool_name,
                        parameters=arguments,
                        allowed=False,
                        reason=confirm_reason,
                    )
                    return Verdict.reject(
                        RefusalCode.CONFIRMATION_DENIED,
                        confirm_reason,
                        retry_hint="User denied the operation. Ask before retrying.",
                    )
                self._audit.log(
                    action="tool_call_confirmed",
                    tool=tool_name,
                    parameters=arguments,
                    allowed=True,
                    reason=f"User confirmed: {confirm_reason}",
                )
            else:
                self._audit.log(
                    action="tool_call",
                    tool=tool_name,
                    parameters=arguments,
                    allowed=True,
                    reason=f"No confirmation callback; proceeding with: {confirm_reason}",
                )

        self._audit.log(
            action="tool_call",
            tool=tool_name,
            parameters=arguments,
            allowed=True,
        )
        return Verdict.accept()

    @staticmethod
    def _classify_safety_reason(reason: str) -> RefusalCode:
        reason_lower = reason.lower()
        if "injection" in reason_lower:
            return RefusalCode.COMMAND_INJECTION
        if "protected" in reason_lower or "access denied" in reason_lower:
            return RefusalCode.PROTECTED_PATH
        return RefusalCode.DANGEROUS_COMMAND
