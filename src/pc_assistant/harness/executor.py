"""Single verified commit boundary for model-proposed tool calls."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pc_assistant.harness.confirm import ConfirmFn
from pc_assistant.harness.refusal import Verdict
from pc_assistant.harness.verifier import Verifier
from pc_assistant.tools.registry import ToolRegistry


def _error_result(tool_name: str, arguments: dict[str, Any], result: Any, registry: ToolRegistry) -> Any:
    """Make every tool failure actionable for the model.

    Tools may keep returning their concise, domain-specific ``error`` text.  The
    execution boundary adds stable structure so the model can distinguish a
    failed call from a normal payload and knows exactly how to retry.
    """
    if not isinstance(result, dict) or "error" not in result:
        return result
    enriched = dict(result)
    enriched.setdefault("tool", tool_name)
    schema = registry.llm_schema(tool_name, skim=False)
    params = schema.get("parameters", {}) if isinstance(schema, dict) else {}
    properties = params.get("properties", {}) if isinstance(params, dict) else {}
    allowed_parameters = list(properties) if isinstance(properties, dict) else []
    if allowed_parameters:
        enriched.setdefault("allowed_parameters", allowed_parameters)
    action_prop = properties.get("action") if isinstance(properties, dict) else None
    if isinstance(action_prop, dict) and isinstance(action_prop.get("enum"), list):
        enriched.setdefault("allowed_actions", action_prop["enum"])
    enriched.setdefault(
        "instruction",
        "Fix the stated problem using the allowed parameters/actions and retry once. "
        "If this is a permission, dependency, or environment failure, report it instead of retrying.",
    )
    return enriched


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
        try:
            result = await self._registry._commit(prepared.tool_name, **prepared.arguments)
        except Exception as exc:
            result = {
                "error": f"Tool execution failed: {exc}",
                "exception_type": type(exc).__name__,
            }
        result = _error_result(prepared.tool_name, prepared.arguments, result, self._registry)
        await self._verifier.post_verify(prepared.tool_name, prepared.arguments)
        return result
