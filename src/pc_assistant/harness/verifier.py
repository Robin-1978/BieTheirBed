"""Deterministic Verifier — the core of the Stochastic-Deterministic Boundary.

Sits between the LLM (proposer) and tool execution (commit).  For every
proposed tool call it runs: schema validation → safety policy → confirmation
gate, and returns a typed ``Verdict`` (accept / reject with refusal code).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from pc_assistant.harness.audit import AuditLogger
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
        confirm_callback: Callable[[str, dict[str, Any]], bool | Awaitable[bool]] | None = None,
    ) -> None:
        self._safety = safety
        self._registry = registry
        self._audit = audit
        self._confirm_callback = confirm_callback

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Verdict:
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
            if self._confirm_callback is not None:
                confirmed = await self._invoke_callback(tool_name, arguments)
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
                reason=safety_result.reason,
            )
            code = self._classify_safety_reason(safety_result.reason)
            return Verdict.reject(
                code,
                safety_result.reason,
                retry_hint="Rephrase the command to avoid dangerous patterns.",
            )

        if need_confirm:
            if self._confirm_callback is not None:
                confirmed = await self._invoke_callback(tool_name, arguments)
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

    async def _invoke_callback(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        try:
            result = self._confirm_callback(tool_name, arguments)  # type: ignore[misc]
            if asyncio.iscoroutine(result):
                return await result  # type: ignore[misc]
            return bool(result)
        except Exception:
            return False

    @staticmethod
    def _classify_safety_reason(reason: str) -> RefusalCode:
        reason_lower = reason.lower()
        if "injection" in reason_lower:
            return RefusalCode.COMMAND_INJECTION
        if "protected" in reason_lower or "access denied" in reason_lower:
            return RefusalCode.PROTECTED_PATH
        return RefusalCode.DANGEROUS_COMMAND
