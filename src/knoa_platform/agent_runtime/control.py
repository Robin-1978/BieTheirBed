"""Principal-scoped runtime control operations."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from knoa_platform.agent_runtime.contracts import (
    ConfigSetRequest,
    ConfigSetResult,
    ExtensionStatusRecord,
    HistoryResult,
    MemoryClearResult,
    MemoryDeleteResult,
    MemoryListResult,
    MemoryRecord,
    MCPResourceCatalogRecord,
    MCPResourceCatalogResult,
    RuntimeScope,
    RuntimeStatus,
    ToolDescriptorRecord,
    ToolListResult,
)
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.context.memory_db import SQLiteMemoryRepository


class RuntimeConfigController(Protocol):
    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult: ...


class ControlService:
    """Control plane with ownership validation before every scoped operation."""

    def __init__(
        self,
        sessions: RuntimeSessionRepository,
        memory: SQLiteMemoryRepository,
        *,
        tool_names: Callable[[RuntimeScope], Iterable[str]],
        config_controller: RuntimeConfigController,
        tool_descriptors: (
            Callable[[RuntimeScope], Iterable[ToolDescriptorRecord]] | None
        ) = None,
        status_details: Callable[[RuntimeScope], dict[str, Any]] | None = None,
        extension_statuses: Callable[[], Iterable[ExtensionStatusRecord]] | None = None,
        mcp_resources: Callable[[], Iterable[MCPResourceCatalogRecord]] | None = None,
        agent_selector: Callable[[str | None], str] | None = None,
        config_admin_principals: frozenset[str] = frozenset({"local"}),
    ) -> None:
        self._sessions = sessions
        self._memory = memory
        self._tool_names = tool_names
        self._tool_descriptors = tool_descriptors
        self._config_controller = config_controller
        self._status_details = status_details
        self._extension_statuses = extension_statuses
        self._mcp_resources = mcp_resources
        self._agent_selector = agent_selector or (lambda requested: requested or "knoa")
        self._config_admin_principals = config_admin_principals

    async def _owned_scope(self, scope: RuntimeScope) -> RuntimeScope:
        return await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )

    async def create_session(
        self,
        principal_id: str,
        *,
        activate: bool = True,
        agent_id: str | None = None,
    ) -> RuntimeScope:
        return await asyncio.to_thread(
            self._sessions.create,
            principal_id,
            activate=activate,
            agent_id=self._agent_selector(agent_id),
        )

    async def get_status(self, scope: RuntimeScope) -> RuntimeStatus:
        owned = await self._owned_scope(scope)
        snapshot = await asyncio.to_thread(self._sessions.load, owned)
        sessions = await asyncio.to_thread(
            self._sessions.list_for_principal,
            owned.principal_id,
        )
        tools = tuple(sorted(set(self._tool_names(owned))))
        details = (
            await asyncio.to_thread(self._status_details, owned)
            if self._status_details is not None
            else {}
        )
        details.update(
            {
                "session_handle": owned.session_handle,
                "messages": len(snapshot.messages),
                "sessions": len(sessions),
                "available_tools": len(tools),
            }
        )
        return RuntimeStatus(
            status="ready",
            connected=True,
            details=details,
            extensions=(
                tuple(self._extension_statuses())
                if self._extension_statuses is not None
                else ()
            ),
        )

    async def get_history(self, scope: RuntimeScope) -> HistoryResult:
        owned = await self._owned_scope(scope)
        snapshot = await asyncio.to_thread(self._sessions.load, owned)
        return HistoryResult(messages=snapshot.messages)

    async def list_memory(self, scope: RuntimeScope) -> MemoryListResult:
        owned = await self._owned_scope(scope)
        rows = await asyncio.to_thread(
            self._memory.list_memories,
            owned.principal_id,
            limit=1000,
        )
        return MemoryListResult(
            memories=tuple(
                MemoryRecord(
                    key=str(row["key"]),
                    value=str(row["value"]),
                    category=str(row["category"]),
                    importance=str(row["importance"]),
                    confidence=float(row["confidence"]),
                    source=str(row["source"]),
                )
                for row in rows
            )
        )

    async def clear_memory(self, scope: RuntimeScope) -> MemoryClearResult:
        owned = await self._owned_scope(scope)
        await asyncio.to_thread(self._memory.clear_memories, owned.principal_id)
        return MemoryClearResult(cleared=True)

    async def delete_memory(self, scope: RuntimeScope, key: str) -> MemoryDeleteResult:
        owned = await self._owned_scope(scope)
        deleted = await asyncio.to_thread(self._memory.delete_memory, owned.principal_id, key)
        return MemoryDeleteResult(deleted=deleted)

    async def list_tools(self, scope: RuntimeScope) -> ToolListResult:
        owned = await self._owned_scope(scope)
        return ToolListResult(
            tools=tuple(sorted(set(self._tool_names(owned)))),
            descriptors=(
                tuple(self._tool_descriptors(owned))
                if self._tool_descriptors is not None
                else ()
            ),
        )

    async def set_config(
        self,
        scope: RuntimeScope,
        request: ConfigSetRequest,
    ) -> ConfigSetResult:
        owned = await self._owned_scope(scope)
        if owned.principal_id not in self._config_admin_principals:
            raise PermissionError("Configuration capability denied")
        return await self._config_controller.set_config(request)

    async def list_mcp_resources(
        self,
        principal_id: str,
    ) -> MCPResourceCatalogResult:
        if not principal_id.strip():
            raise PermissionError("Principal is required")
        resources = (
            tuple(self._mcp_resources())
            if self._mcp_resources is not None
            else ()
        )
        return MCPResourceCatalogResult(resources=resources)
