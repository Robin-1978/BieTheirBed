"""Deterministic Verifier — the core of the Stochastic-Deterministic Boundary.

Sits between the LLM (proposer) and tool execution (commit).  For every
proposed tool call it runs: schema validation → safety policy → confirmation
gate, and returns a typed ``Verdict`` (accept / reject with refusal code).

It also carries an optional post-verify strategy for GUI automation.  The
caller must invoke :meth:`post_verify` only after a verified tool execution has
completed; authorization and postcondition evidence are deliberately separate
lifecycle steps.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.confirm import ConfirmFn, resolve_confirm
from pc_assistant.harness.refusal import RefusalCode, Verdict
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.tools.registry import ToolRegistry

# High-risk GUI actions that warrant a post-action screen verification.
RISKY_GUI_ACTIONS: dict[str, set[str]] = {
    "mouse": {"click", "double_click", "right_click", "drag"},
    "keyboard": {"hotkey", "press", "write"},
    "ui": {"click", "type"},
}

PostVerifyFn = Callable[[str, dict[str, Any]], Awaitable[str]]


class Verifier:
    """Deterministic gate between LLM proposals and tool execution."""

    def __init__(
        self,
        safety: SafetyChecker,
        registry: ToolRegistry,
        audit: AuditLogger,
        confirm_callback: ConfirmFn | None = None,
        *,
        verify_enabled: bool = False,
        post_verify_callback: PostVerifyFn | None = None,
    ) -> None:
        self._safety = safety
        self._registry = registry
        self._audit = audit
        self._confirm_callback = confirm_callback
        self._verify_enabled = verify_enabled
        self._post_verify_callback = post_verify_callback

    @staticmethod
    def _needs_post_verify(tool_name: str, arguments: dict[str, Any]) -> bool:
        risky_actions = RISKY_GUI_ACTIONS.get(tool_name)
        if risky_actions is None:
            return False
        return arguments.get("action") in risky_actions

    async def post_verify(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Collect advisory postcondition evidence after tool execution."""
        if not self._verify_enabled or self._post_verify_callback is None:
            return
        if not self._needs_post_verify(tool_name, arguments):
            return
        try:
            summary = await self._post_verify_callback(tool_name, arguments)
        except Exception as e:  # post-verify is advisory; never fail the turn
            summary = f"post-verify failed: {e}"
        self._audit.log(
            action="tool_call_verified",
            tool=tool_name,
            parameters=arguments,
            allowed=True,
            reason=summary,
        )

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
                # Fail closed: no confirmation gate available => treat as denied.
                self._audit.log(
                    action="tool_call_blocked",
                    tool=tool_name,
                    parameters=arguments,
                    allowed=False,
                    reason=f"Confirmation required but no confirmation gate is available: {confirm_reason}",
                )
                return Verdict.reject(
                    RefusalCode.CONFIRMATION_REQUIRED,
                    confirm_reason,
                    retry_hint="No confirmation mechanism is configured; run this in an interactive client with a confirmation gate.",
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
