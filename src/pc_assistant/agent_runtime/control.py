"""Principal-scoped runtime control operations."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Protocol

from pc_assistant.agent_runtime.contracts import (
    ConfigSetRequest,
    ConfigSetResult,
    HistoryResult,
    MemoryClearResult,
    MemoryListResult,
    MemoryRecord,
    RuntimeScope,
    RuntimeStatus,
    ToolListResult,
)
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.context.memory_db import SQLiteMemoryRepository


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
        config_admin_principals: frozenset[str] = frozenset({"local"}),
    ) -> None:
        self._sessions = sessions
        self._memory = memory
        self._tool_names = tool_names
        self._config_controller = config_controller
        self._config_admin_principals = config_admin_principals

    async def _owned_scope(self, scope: RuntimeScope) -> RuntimeScope:
        return await asyncio.to_thread(
            self._sessions.resolve,
            scope.principal_id,
            scope.session_handle,
        )

    async def create_session(self, principal_id: str) -> RuntimeScope:
        return await asyncio.to_thread(self._sessions.create, principal_id)

    async def get_status(self, scope: RuntimeScope) -> RuntimeStatus:
        owned = await self._owned_scope(scope)
        snapshot = await asyncio.to_thread(self._sessions.load, owned)
        return RuntimeStatus(
            status="ready",
            connected=True,
            details={
                "session_handle": owned.session_handle,
                "messages": len(snapshot.messages),
            },
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

    async def list_tools(self, scope: RuntimeScope) -> ToolListResult:
        owned = await self._owned_scope(scope)
        return ToolListResult(
            tools=tuple(sorted(set(self._tool_names(owned))))
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
