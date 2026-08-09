"""Confirmation-gated import of one manually selected local MCP package."""
from __future__ import annotations

from typing import Any

from pc_assistant.extensions.mcp_package import MCPPackageService
from pc_assistant.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk


class MCPImportTool(ToolBase):
    name = "mcp_import"
    description = (
        "Import and activate a local standard MCP package after user confirmation. "
        "The source is copied into Knoa's managed runtime; hidden metadata and "
        "symlinks are omitted, while oversized packages are rejected."
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
        path = str(kwargs.get("path", "")).strip()
        server_id = str(kwargs.get("server_id", "")).strip()
        try:
            status = await self._packages.import_local(path, server_id)
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}
        return {
            "installed": True,
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
                    "path": {
                        "type": "string",
                        "description": "Existing local directory containing mcp.yaml.",
                    },
                    "server_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,23}$",
                        "description": "Stable ID used for the installed MCP namespace.",
                    },
                },
                "required": ["path", "server_id"],
                "additionalProperties": False,
            },
        }
