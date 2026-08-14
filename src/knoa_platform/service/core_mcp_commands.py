"""Explicit owner-operated MCP package management commands."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.extensions.mcp_package import MCPPackageService
from knoa_platform.extensions.models import MCPResourceTaskConfig
from knoa_platform.service.core_api import (
    CoreError,
    DeployMCPPackageRequest,
    MCPPackageDeployedMessage,
    MCPPackageDeploymentSnapshot,
)

Send = Callable[[Any], Awaitable[None]]


class MCPPackageCommandHandler:
    """Keep explicit local administration separate from Agent Tool calls."""

    def __init__(
        self,
        packages: MCPPackageService,
        sessions: RuntimeSessionRepository,
        *,
        owner_principal_id: str,
    ) -> None:
        self._packages = packages
        self._sessions = sessions
        self._owner_principal_id = owner_principal_id

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        if not isinstance(request, DeployMCPPackageRequest):
            return False
        if principal != self._owner_principal_id:
            raise PermissionError("MCP package deployment is owner-only")
        try:
            route = None
            if request.resource_uri:
                await asyncio.to_thread(
                    self._sessions.resolve,
                    principal,
                    request.session_handle,
                )
                route = (
                    request.route_id,
                    MCPResourceTaskConfig(
                        uri=request.resource_uri,
                        principal_id=principal,
                        session_handle=request.session_handle,
                        include_root=request.include_root,
                        tools_enabled=request.tools_enabled,
                        priority=request.priority,
                    ),
                )
            action, status = await self._packages.deploy_local(
                request.path,
                request.server_id,
                route=route,
            )
        except (OSError, ValueError) as exc:
            await send(
                CoreError(
                    request_id=request.request_id,
                    code="invalid_request",
                    message=str(exc),
                    correlation_id=uuid.uuid4().hex,
                )
            )
            return True
        await send(
            MCPPackageDeployedMessage(
                request_id=request.request_id,
                deployment=MCPPackageDeploymentSnapshot(
                    action=action,
                    server_id=request.server_id,
                    extension_id=status.descriptor.extension_id,
                    state=status.state.value,
                    tools=status.tools,
                    detail=status.detail,
                    resource_task=request.resource_uri,
                ),
            )
        )
        return True
