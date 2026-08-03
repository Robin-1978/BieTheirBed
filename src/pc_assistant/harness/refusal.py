"""Typed refusal codes for the Stochastic-Deterministic Boundary.

Every reject signal carries a machine-readable code, a human reason, and an
optional retry hint so downstream systems (LLM, audit, UI) can react without
string-grepping ad-hoc messages.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class RefusalCode(str, Enum):
    DANGEROUS_COMMAND = "dangerous_command"
    PROTECTED_PATH = "protected_path"
    COMMAND_INJECTION = "command_injection"
    TOOL_NOT_FOUND = "tool_not_found"
    INVALID_ARGUMENTS = "invalid_arguments"
    CONFIRMATION_DENIED = "confirmation_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    RATE_LIMITED = "rate_limited"
    IDEMPOTENCY_HIT = "idempotency_hit"
    LOOP_DETECTED = "loop_detected"


class Verdict:
    """Immutable result of a Verifier check: accepted or rejected."""

    __slots__ = ("_rejected", "_code", "_reason", "_retry_hint", "_cached_result")

    def __init__(
        self,
        rejected: bool,
        code: RefusalCode | None = None,
        reason: str = "",
        retry_hint: str = "",
        cached_result: Any = None,
    ) -> None:
        self._rejected = rejected
        self._code = code
        self._reason = reason
        self._retry_hint = retry_hint
        self._cached_result = cached_result

    @property
    def rejected(self) -> bool:
        return self._rejected

    @property
    def accepted(self) -> bool:
        return not self._rejected

    @property
    def code(self) -> RefusalCode | None:
        return self._code

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def retry_hint(self) -> str:
        return self._retry_hint

    @property
    def cached_result(self) -> Any:
        return self._cached_result

    def to_structured_message(self) -> str:
        parts = [f"[REJECTED:{self._code.value if self._code else 'unknown'}]"]
        if self._reason:
            parts.append(self._reason)
        if self._retry_hint:
            parts.append(f"Suggestion: {self._retry_hint}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rejected": self._rejected,
            "code": self._code.value if self._code else None,
            "reason": self._reason,
            "retry_hint": self._retry_hint,
        }

    @staticmethod
    def accept(cached_result: Any = None) -> Verdict:
        return Verdict(rejected=False, cached_result=cached_result)

    @staticmethod
    def reject(
        code: RefusalCode,
        reason: str,
        retry_hint: str = "",
    ) -> Verdict:
        return Verdict(rejected=True, code=code, reason=reason, retry_hint=retry_hint)
