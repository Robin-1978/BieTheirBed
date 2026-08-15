"""Agent-facing governed subagent delegation tools."""

from __future__ import annotations

import asyncio
from typing import Any

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import current_tool_step_context
from knoa_platform.agents.delegation import DelegationService
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class SpawnSubagentTool(ToolBase):
    name = "spawn_subagent"
    description = (
        "Create one governed Child Task using an explicitly allowed delegate Agent."
    )
    details = (
        "Pass only the context the child needs. Task parents must use detached mode; "
        "Conversation parents may use join and then call subagent(action='await')."
    )
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, delegations: DelegationService) -> None:
        self._delegations = delegations

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Subagent delegation requires an authenticated scope"}

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        tool_context = current_tool_step_context()
        if tool_context is None:
            return {"error": "Delegation parent invocation is unavailable"}
        try:
            link = await self._delegations.spawn(
                scope,
                tool_context.run_id,
                target_agent_id=str(kwargs["target_agent_id"]),
                goal=str(kwargs["goal"]),
                context=dict(kwargs.get("context") or {}),
                requested_capabilities=frozenset(
                    str(item) for item in kwargs.get("requested_capabilities") or ()
                ),
                requested_tools=frozenset(
                    str(item) for item in kwargs.get("requested_platform_tools") or ()
                ),
                requested_skills=frozenset(
                    str(item) for item in kwargs.get("requested_skills") or ()
                ),
                deadline_seconds=float(kwargs.get("deadline_seconds") or 120.0),
                mode=str(kwargs.get("mode") or "detached"),
                idempotency_key=str(kwargs["idempotency_key"]),
            )
        except (KeyError, LookupError, PermissionError, ValueError) as exc:
            return {"error": str(exc)}
        return {
            "delegation_id": link.delegation_id,
            "child_task_id": link.child_task_id,
            "mode": link.mode,
            "status": "queued",
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_agent_id": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9_-]{0,63}$",
                    },
                    "goal": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "context": {"type": "object", "maxProperties": 32},
                    "requested_capabilities": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 128},
                        "maxItems": 64,
                        "uniqueItems": True,
                    },
                    "requested_platform_tools": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 256},
                        "maxItems": 64,
                        "uniqueItems": True,
                    },
                    "requested_skills": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 128},
                        "maxItems": 32,
                        "uniqueItems": True,
                    },
                    "deadline_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 86400,
                    },
                    "mode": {"type": "string", "enum": ["join", "detached"]},
                    "idempotency_key": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                },
                "required": [
                    "target_agent_id",
                    "goal",
                    "mode",
                    "idempotency_key",
                ],
                "additionalProperties": False,
            },
        }


class SubagentTool(ToolBase):
    name = "subagent"
    description = "Get, await, or cancel a Child Task created by this invocation."
    effect = ToolEffect.INTERNAL_WRITE
    capabilities = frozenset({ToolCapability.TASK_MANAGEMENT})
    risk = ToolRisk.LOW

    def __init__(self, delegations: DelegationService) -> None:
        self._delegations = delegations

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "Subagent control requires an authenticated scope"}

    async def execute_scoped(self, scope: RuntimeScope, **kwargs: Any) -> Any:
        tool_context = current_tool_step_context()
        if tool_context is None:
            return {"error": "Delegation parent invocation is unavailable"}
        action = str(kwargs.get("action") or "get")
        delegation_id = str(kwargs.get("delegation_id") or "")
        try:
            if action == "cancel":
                return await self._delegations.cancel(
                    scope,
                    tool_context.run_id,
                    delegation_id,
                )
            result = await self._delegations.result(
                scope,
                tool_context.run_id,
                delegation_id,
            )
            if action != "await":
                return result
            if result.get("mode") != "join":
                return {"error": "Only join delegations can be awaited"}
            if (
                self._delegations.parent_kind(
                    scope.principal_id,
                    tool_context.run_id,
                )
                != "conversation_turn"
            ):
                return {"error": "Task parents cannot await Child Tasks"}
            while result["status"] not in {"completed", "failed", "cancelled"}:
                if tool_context.cancellation.is_set():
                    await self._delegations.cancel(
                        scope,
                        tool_context.run_id,
                        delegation_id,
                    )
                    return {"error": "Parent invocation was cancelled"}
                await asyncio.sleep(0.1)
                result = await self._delegations.result(
                    scope,
                    tool_context.run_id,
                    delegation_id,
                )
            return result
        except (LookupError, PermissionError, ValueError) as exc:
            return {"error": str(exc)}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "await", "cancel"],
                    },
                    "delegation_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                    },
                },
                "required": ["action", "delegation_id"],
                "additionalProperties": False,
            },
        }
