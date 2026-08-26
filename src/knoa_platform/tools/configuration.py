"""Governed configuration tool exposed to the local Knoa Agent.

The Agent never writes configuration files directly.  It can inspect the
current revision, create a validated draft from a structured patch, and ask
the user to confirm a publish call.  The normal ToolStep confirmation boundary
protects publish/rollback operations.
"""

from __future__ import annotations

import copy
from typing import Any

from knoa_platform.configuration import ConfigurationService
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolPolicy, ToolRisk


def _merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        result = copy.deepcopy(base)
        for key, value in patch.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = _merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    return copy.deepcopy(patch)


class ConfigurationTool(ToolBase):
    name = "configuration"
    description = (
        "Inspect Knoa configuration, create a validated draft, or publish a "
        "previously reviewed draft. Publishing always requires explicit user confirmation."
    )
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.MEDIUM

    def __init__(self, service: ConfigurationService, *, enabled: bool = False) -> None:
        self._service = service
        self._enabled = enabled

    @property
    def policy(self) -> ToolPolicy:
        enabled = self._service.current().document.operational.agent_configuration_enabled
        if not self._enabled and not enabled:
            return ToolPolicy(
                effect=ToolEffect.UNKNOWN,
                capabilities=self.capabilities,
                risk=self.risk,
            )
        return super().policy

    def policy_for(self, arguments: dict[str, Any]) -> ToolPolicy:
        action = str(arguments.get("action", "inspect"))
        if action in {"publish", "rollback"}:
            return ToolPolicy(
                effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
                capabilities=self.capabilities,
                risk=ToolRisk.HIGH,
            )
        return self.policy

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["describe", "inspect", "propose", "publish", "rollback"],
                    },
                    "changes": {"type": "object"},
                    "draft_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "expected_version": {"type": "integer", "minimum": 0},
                    "revision_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "summary": {"type": "string", "maxLength": 512},
                },
                "required": ["action"],
            },
        }

    async def execute_scoped(self, scope: Any, **kwargs: Any) -> Any:
        action = str(kwargs.get("action", "inspect"))
        if action == "describe":
            return {
                "purpose": "Translate a user's natural-language configuration request into a safe draft.",
                "workflow": ["describe/inspect", "propose", "show returned changes to user", "publish after explicit confirmation"],
                "sections": {
                    "providers": "LLM provider endpoints and secret references",
                    "models": "model aliases and provider bindings",
                    "agents": "Knoa/Codex prompts, tools, capabilities and delegation",
                    "approval_review": "Reviewer mode, agent, timeout and risk threshold",
                    "operational": "budgets, limits and opt-in agent_configuration_enabled",
                    "mcp_servers": "MCP transports and per-tool policies",
                    "skills": "installed skill package references",
                },
                "propose_input": "changes is a partial object merged with the current document; never include secrets, only secret_refs.",
            }
        if action == "inspect":
            revision = self._service.current()
            return {
                "revision_id": revision.revision_id,
                "revision_number": revision.revision_number,
                "state": self._service.state().model_dump(mode="json"),
                "document": revision.document.model_dump(mode="json"),
            }
        if action == "propose":
            changes = kwargs.get("changes")
            if not isinstance(changes, dict) or not changes:
                return {"error": "changes must be a non-empty object"}
            current = self._service.current()
            candidate_data = _merge(current.document.model_dump(mode="python"), changes)
            try:
                candidate = type(current.document).model_validate(candidate_data)
            except Exception as exc:  # validation details are safe to return to the Agent
                return {"error": "invalid_configuration", "detail": str(exc)}
            draft = self._service.create_draft(actor=scope.principal_id)
            draft = self._service.replace_draft(
                draft.draft_id,
                candidate,
                expected_version=draft.draft_version,
                actor=scope.principal_id,
            )
            preflight = await self._service.preflight(draft.draft_id)
            if not preflight.valid:
                return {
                    "error": "preflight_failed",
                    "draft_id": draft.draft_id,
                    "draft_version": draft.draft_version,
                    "issues": [item.model_dump(mode="json") for item in preflight.issues],
                }
            before = current.document.model_dump(mode="json")
            after = candidate.model_dump(mode="json")
            return {
                "draft_id": draft.draft_id,
                "draft_version": draft.draft_version,
                "changes": _diff_documents(before, after),
                "next": "Call configuration(action='publish', draft_id=..., expected_version=...) after user confirmation.",
            }
        if action == "publish":
            draft_id = str(kwargs.get("draft_id", ""))
            if not draft_id:
                return {"error": "draft_id is required"}
            expected = int(kwargs.get("expected_version", -1))
            if expected < 0:
                return {"error": "expected_version is required"}
            result = await self._service.publish(
                draft_id,
                expected_version=expected,
                actor=scope.principal_id,
                summary=str(kwargs.get("summary") or "Knoa Agent configuration update")[:512],
            )
            return {
                "revision_id": result.revision.revision_id,
                "state": result.state.model_dump(mode="json"),
            }
        if action == "rollback":
            revision_id = str(kwargs.get("revision_id", ""))
            if not revision_id:
                return {"error": "revision_id is required"}
            result = await self._service.rollback(
                revision_id,
                actor=scope.principal_id,
                summary=str(kwargs.get("summary") or "Knoa Agent configuration rollback")[:512],
            )
            return {
                "revision_id": result.revision.revision_id,
                "state": result.state.model_dump(mode="json"),
            }
        return {"error": f"unsupported action: {action}"}

    async def execute(self, **kwargs: Any) -> Any:
        # ToolStep always invokes execute_scoped; keep this for direct callers.
        return await self.execute_scoped(None, **kwargs)


def _diff_documents(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                child = f"{path}/{key}"
                if key not in left:
                    changes.append({"op": "add", "path": child, "value": right[key]})
                elif key not in right:
                    changes.append({"op": "remove", "path": child})
                else:
                    walk(left[key], right[key], child)
            return
        if left != right:
            changes.append({"op": "replace", "path": path or "/", "old": left, "value": right})

    walk(before, after, "")
    return changes


__all__ = ["ConfigurationTool"]
