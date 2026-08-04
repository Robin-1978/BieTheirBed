"""Single verified commit boundary for model-proposed tool calls."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pc_assistant.harness.confirm import ConfirmFn
from pc_assistant.harness.refusal import Verdict
from pc_assistant.harness.verifier import Verifier
from pc_assistant.tools.registry import ToolRegistry


@dataclass
class PreparedToolCall:
    tool_name: str
    arguments: dict[str, Any]
    _capability: object
    _consumed: bool = False


class VerifiedToolExecutor:
    """Authorize once, then consume an opaque capability for exactly one commit."""

    def __init__(self, verifier: Verifier, registry: ToolRegistry) -> None:
        self._verifier = verifier
        self._registry = registry
        self._capability = object()

    async def authorize(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        confirm_callback: ConfirmFn | None = None,
    ) -> tuple[Verdict, PreparedToolCall | None]:
        verdict = await self._verifier.verify(
            tool_name,
            arguments,
            confirm_callback=confirm_callback,
        )
        if verdict.rejected:
            return verdict, None
        return verdict, PreparedToolCall(
            tool_name=tool_name,
            arguments=dict(arguments),
            _capability=self._capability,
        )

    async def commit(self, prepared: PreparedToolCall) -> Any:
        if prepared._capability is not self._capability:
            raise PermissionError("Tool call was not authorized by this executor")
        if prepared._consumed:
            raise RuntimeError("Authorized tool call has already been committed")
        prepared._consumed = True
        result = await self._registry._commit(prepared.tool_name, **prepared.arguments)
        await self._verifier.post_verify(prepared.tool_name, prepared.arguments)
        return result
