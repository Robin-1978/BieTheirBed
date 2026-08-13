"""Confirmation-gated deployment of one manually selected local MCP package."""
from __future__ import annotations

from typing import Any

from knoa_platform.extensions.mcp_package import MCPPackageService
from knoa_platform.extensions.models import MCPResourceTaskConfig
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class MCPDeployTool(ToolBase):
    name = "mcp_deploy"
    description = (
        "Install or atomically update and activate a local standard MCP package "
        "after user confirmation. The source is copied into Knoa's managed runtime; "
        "hidden metadata and symlinks are omitted, oversized packages are rejected, "
        "and a failed update restores the previous running package."
    )
    effect = ToolEffect.LOCAL_WRITE
    capabilities = frozenset(
        {
            ToolCapability.HOST_READ,
            ToolCapability.HOST_WRITE,
            ToolCapability.MCP,
        }
    )
    risk = ToolRisk.HIGH

    def __init__(self, packages: MCPPackageService) -> None:
        self._packages = packages

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        return {"error": "This operation requires a principal-owned Session"}

    async def execute_scoped(self, scope: Any, **kwargs: Any) -> Any:
        path = str(kwargs.get("path", "")).strip()
        server_id = str(kwargs.get("server_id", "")).strip()
        try:
            resource_uri = str(kwargs.get("resource_uri", "")).strip()
            route = None
            if resource_uri:
                route_id = str(kwargs.get("route_id", "events")).strip()
                route = (
                    route_id,
                    MCPResourceTaskConfig.model_validate(
                        {
                            "uri": resource_uri,
                            "principal_id": scope.principal_id,
                            "session_handle": scope.session_handle,
                            "include_root": bool(kwargs.get("include_root", False)),
                            "tools_enabled": bool(kwargs.get("tools_enabled", True)),
                            "priority": int(kwargs.get("priority", 0)),
                        }
                    ),
                )
            action, status = await self._packages.deploy_local(
                path,
                server_id,
                route=route,
            )
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
        return {
            "deployed": True,
            "action": action,
            "server_id": server_id,
            "extension_id": status.descriptor.extension_id,
            "state": status.state.value,
            "tools": list(status.tools),
            "detail": status.detail,
            "resource_task": resource_uri or None,
        }

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "server_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,23}$",
                    },
                    "resource_uri": {"type": "string"},
                    "route_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$",
                        "default": "events",
                    },
                    "include_root": {"type": "boolean", "default": False},
                    "tools_enabled": {"type": "boolean", "default": True},
                    "priority": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 9,
                        "default": 0,
                    },
                },
                "required": ["path", "server_id"],
                "additionalProperties": False,
            },
        }
