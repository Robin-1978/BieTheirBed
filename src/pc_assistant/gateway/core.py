"""Principal-scoped, explicitly allow-listed Secure Gateway bridge to Core."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from pc_assistant.config import AppConfig
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_api import (
    ApprovalResolvedMessage,
    ArtifactInputRef,
    TaskAcceptedMessage,
    TaskCancelResultMessage,
    TaskListMessage,
    TaskSnapshot,
)
from pc_assistant.service.core_client import CoreClient
from pc_assistant.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from pc_assistant.tasks import TaskState
from pc_assistant.tasks import PrincipalTaskEvent


class GatewayCoreClient(Protocol):
    is_connected: bool

    async def create_session(self) -> str: ...

    async def create_task(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        tools_enabled: bool = True,
        priority: int = 0,
        parent_task_id: str = "",
    ) -> TaskAcceptedMessage: ...

    async def get_task(self, task_id: str) -> TaskSnapshot: ...

    async def list_tasks(
        self,
        *,
        session_handle: str = "",
        state: TaskState | None = None,
        limit: int = 50,
        cursor: str = "",
    ) -> TaskListMessage: ...

    async def cancel_task(
        self,
        task_id: str,
        *,
        reason: str = "",
    ) -> TaskCancelResultMessage: ...

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> ApprovalResolvedMessage: ...

    def principal_task_events(
        self,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[PrincipalTaskEvent]: ...

    async def disconnect(self) -> None: ...


ClientFactory = Callable[[str], Awaitable[GatewayCoreClient]]


class GatewayCoreBridge:
    """Map a fixed mobile workbench surface onto principal-owned Core calls."""

    def __init__(
        self,
        config: AppConfig,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._config = config
        self._paths = RuntimePaths.from_root(config.runtime_root)
        self._client_factory = client_factory or self._connect_client
        self._clients: dict[str, GatewayCoreClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        clients, self._clients = tuple(self._clients.values()), {}
        await asyncio.gather(
            *(client.disconnect() for client in clients),
            return_exceptions=True,
        )

    async def create_session(self, principal_id: str) -> str:
        return await (await self._client_for(principal_id)).create_session()

    async def create_task(
        self,
        principal_id: str,
        session_handle: str,
        user_input: str,
        attachments: tuple[ArtifactInputRef, ...],
        *,
        tools_enabled: bool,
        priority: int,
        parent_task_id: str,
    ) -> TaskAcceptedMessage:
        client = await self._client_for(principal_id)
        return await client.create_task(
            session_handle,
            user_input,
            attachments,
            tools_enabled=tools_enabled,
            priority=priority,
            parent_task_id=parent_task_id,
        )

    async def get_task(self, principal_id: str, task_id: str) -> TaskSnapshot:
        return await (await self._client_for(principal_id)).get_task(task_id)

    async def list_tasks(
        self,
        principal_id: str,
        *,
        session_handle: str,
        state: TaskState | None,
        limit: int,
        cursor: str,
    ) -> TaskListMessage:
        client = await self._client_for(principal_id)
        return await client.list_tasks(
            session_handle=session_handle,
            state=state,
            limit=limit,
            cursor=cursor,
        )

    async def cancel_task(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str,
    ) -> TaskCancelResultMessage:
        client = await self._client_for(principal_id)
        return await client.cancel_task(task_id, reason=reason)

    async def resolve_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> ApprovalResolvedMessage:
        client = await self._client_for(principal_id)
        return await client.resolve_approval(approval_id, approved=approved)

    async def principal_task_events(
        self,
        principal_id: str,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[PrincipalTaskEvent]:
        client = await self._client_for(principal_id)
        async for event in client.principal_task_events(after_id=after_id):
            yield event

    async def _client_for(self, principal_id: str) -> GatewayCoreClient:
        lock = self._locks.setdefault(principal_id, asyncio.Lock())
        async with lock:
            current = self._clients.get(principal_id)
            if current is not None and current.is_connected:
                return current
            if current is not None:
                await current.disconnect()
            client = await self._client_factory(principal_id)
            self._clients[principal_id] = client
            return client

    async def _connect_client(self, principal_id: str) -> CoreClient:
        signing_key = resolve_local_service_token(self._paths)
        credential = issue_principal_credential(signing_key, principal_id)
        return await CoreClient.connect(
            f"ws://{self._config.service_host}:{self._config.service_port}",
            credential,
        )
