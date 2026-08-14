"""Confirmation-gated deployment of one manually selected local MCP package."""
from __future__ import annotations

from typing import Any

from knoa_platform.extensions.mcp_package import MCPPackageService
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
        del scope
        path = str(kwargs.get("path", "")).strip()
        server_id = str(kwargs.get("server_id", "")).strip()
        try:
            action, status = await self._packages.deploy_local(
                path,
                server_id,
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
                },
                "required": ["path", "server_id"],
                "additionalProperties": False,
            },
        }
