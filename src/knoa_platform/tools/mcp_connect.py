"""Confirmation-gated onboarding of a standard MCP server."""

from __future__ import annotations

from typing import Any

from knoa_platform.extensions.mcp_onboarding import MCPOnboardingService
from knoa_platform.extensions.models import MCPServerConfig
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


def _connection_config(kwargs: dict[str, Any]) -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {
            "enabled": True,
            "transport": kwargs.get("transport", "streamable_http"),
            "url": kwargs.get("url", ""),
            "command": kwargs.get("command", ""),
            "args": kwargs.get("args", []),
            "working_directory": kwargs.get("working_directory", ""),
            "inherit_env": kwargs.get("inherit_env", []),
            "timeout_seconds": kwargs.get("timeout_seconds", 30),
        }
    )


def _connection_properties() -> dict[str, Any]:
    return {
        "transport": {
            "type": "string",
            "enum": ["streamable_http", "stdio"],
        },
        "url": {"type": "string"},
        "command": {"type": "string"},
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 64,
        },
        "working_directory": {"type": "string"},
        "inherit_env": {
            "type": "array",
            "items": {
                "type": "string",
                "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,127}$",
            },
            "maxItems": 64,
        },
        "timeout_seconds": {
            "type": "number",
            "minimum": 1,
            "maximum": 300,
        },
    }


class MCPInspectTool(ToolBase):
    name = "mcp_inspect"
    description = (
        "Inspect a standard MCP server without persisting or enabling it. Returns "
        "Tools with annotations, Resources and Prompts for a later confirmed connect."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.NETWORK, ToolCapability.MCP})
    risk = ToolRisk.LOW

    def __init__(self, onboarding: MCPOnboardingService) -> None:
        self._onboarding = onboarding

    async def execute(self, **kwargs: Any) -> Any:
        try:
            result = await self._onboarding.inspect(_connection_config(kwargs))
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "read_only_hint": tool.read_only_hint,
                    "destructive_hint": tool.destructive_hint,
                    "idempotent_hint": tool.idempotent_hint,
                    "open_world_hint": tool.open_world_hint,
                }
                for tool in result.tools
            ],
            "resources": [
                {"uri": resource.uri, "name": resource.name}
                for resource in result.resources
            ],
            "prompts": [prompt.name for prompt in result.prompts],
            "next_step": (
                "Call mcp_connect with the exact selected read-only Tool names. "
                "The user will confirm that explicit list before persistence."
            ),
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": _connection_properties(),
                "required": ["transport"],
                "additionalProperties": False,
            },
        }


class MCPConnectTool(ToolBase):
    name = "mcp_connect"
    description = (
        "Connect, discover, persist and activate a standard MCP server after user "
        "confirmation. Only tools explicitly annotated readOnlyHint=true are enabled "
        "automatically; write or ambiguous tools remain disabled."
    )
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset(
        {
            ToolCapability.HOST_WRITE,
            ToolCapability.NETWORK,
            ToolCapability.MCP,
        }
    )
    risk = ToolRisk.HIGH

    def __init__(self, onboarding: MCPOnboardingService) -> None:
        self._onboarding = onboarding

    async def execute(self, **kwargs: Any) -> Any:
        server_id = str(kwargs.get("server_id", "")).strip()
        try:
            selected = frozenset(str(name) for name in kwargs.get("enabled_tools", []))
            result = await self._onboarding.connect(
                server_id,
                _connection_config(kwargs),
                selected,
            )
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
        return {
            "connected": result.state == "running",
            "server_id": result.server_id,
            "state": result.state,
            "enabled_read_only_tools": list(result.enabled_tools),
            "withheld_tools": list(result.withheld_tools),
            "resources": [
                {
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                }
                for resource in result.resources
            ],
            "prompts": [prompt.name for prompt in result.prompts],
            "detail": result.detail,
            "automation_note": (
                "Create or update a Task Definition with event_source=mcp:"
                f"{result.server_id} and a selected resource_uri_prefix."
            ),
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,23}$",
                    },
                    "enabled_tools": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    **_connection_properties(),
                },
                "required": ["server_id", "transport", "enabled_tools"],
                "additionalProperties": False,
            },
        }


class MCPDisableTool(ToolBase):
    name = "mcp_disable"
    description = (
        "Disable a connected MCP server, remove its active Tools, and persist the "
        "disabled state after user confirmation."
    )
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset({ToolCapability.HOST_WRITE, ToolCapability.MCP})
    risk = ToolRisk.HIGH

    def __init__(self, onboarding: MCPOnboardingService) -> None:
        self._onboarding = onboarding

    async def execute(self, **kwargs: Any) -> Any:
        server_id = str(kwargs.get("server_id", "")).strip()
        try:
            await self._onboarding.disable_server(server_id)
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
        return {"disabled": True, "server_id": server_id}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,23}$",
                    }
                },
                "required": ["server_id"],
                "additionalProperties": False,
            },
        }
