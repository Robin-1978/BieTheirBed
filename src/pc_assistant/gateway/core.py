"""Principal-scoped, explicitly allow-listed Secure Gateway bridge to Core."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from pc_assistant.config import AppConfig
from pc_assistant.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
    RuntimeStatus,
    ToolListResult,
)
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_api import (
    ApprovalResolvedMessage,
    ArtifactInputRef,
    TaskAcceptedMessage,
    TaskCancelResultMessage,
    TaskListMessage,
    TaskPauseResultMessage,
    TaskResumedMessage,
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

    async def pause_task(
        self,
        task_id: str,
        *,
        reason: str = "",
    ) -> TaskPauseResultMessage: ...

    async def resume_task(
        self,
        task_id: str,
        *,
        reason: str = "",
        acknowledge_outcome_unknown: bool = False,
    ) -> TaskResumedMessage: ...

    async def transcribe_artifact(
        self,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactTranscriptionResult: ...

    async def status(self, session_handle: str) -> RuntimeStatus: ...

    async def list_tools(self, session_handle: str) -> ToolListResult: ...

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> ApprovalResolvedMessage: ...

    async def upload_artifact(
        self,
        session_handle: str,
        data_url: str,
        *,
        media_type: str,
        name: str,
        caption: str,
    ) -> ArtifactRef: ...

    async def download_artifact(
        self,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactDownloadResult: ...

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

    async def pause_task(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str,
    ) -> TaskPauseResultMessage:
        return await (await self._client_for(principal_id)).pause_task(
            task_id,
            reason=reason,
        )

    async def resume_task(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str,
        acknowledge_outcome_unknown: bool,
    ) -> TaskResumedMessage:
        return await (await self._client_for(principal_id)).resume_task(
            task_id,
            reason=reason,
            acknowledge_outcome_unknown=acknowledge_outcome_unknown,
        )

    async def retry_task(
        self,
        principal_id: str,
        task_id: str,
        *,
        reason: str,
    ) -> TaskAcceptedMessage:
        client = await self._client_for(principal_id)
        previous = await client.get_task(task_id)
        if previous.state not in {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }:
            raise ValueError("Only terminal Tasks can be retried")
        goal = previous.goal
        if reason.strip():
            goal += f"\n\nRetry note: {reason.strip()}"
        return await client.create_task(
            previous.session_handle,
            goal,
            previous.attachments,
            tools_enabled=previous.tools_enabled,
            priority=previous.priority,
            parent_task_id=previous.task_id,
        )

    async def transcribe_artifact(
        self,
        principal_id: str,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactTranscriptionResult:
        return await (await self._client_for(principal_id)).transcribe_artifact(
            session_handle,
            artifact_id,
        )

    async def status(
        self,
        principal_id: str,
        session_handle: str,
    ) -> RuntimeStatus:
        return await (await self._client_for(principal_id)).status(session_handle)

    async def list_tools(
        self,
        principal_id: str,
        session_handle: str,
    ) -> ToolListResult:
        return await (await self._client_for(principal_id)).list_tools(session_handle)

    async def resolve_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> ApprovalResolvedMessage:
        client = await self._client_for(principal_id)
        return await client.resolve_approval(approval_id, approved=approved)

    async def upload_artifact(
        self,
        principal_id: str,
        session_handle: str,
        data_url: str,
        *,
        media_type: str,
        name: str,
        caption: str,
    ) -> ArtifactRef:
        client = await self._client_for(principal_id)
        return await client.upload_artifact(
            session_handle,
            data_url,
            media_type=media_type,
            name=name,
            caption=caption,
        )

    async def download_artifact(
        self,
        principal_id: str,
        session_handle: str,
        artifact_id: str,
    ) -> ArtifactDownloadResult:
        client = await self._client_for(principal_id)
        return await client.download_artifact(session_handle, artifact_id)

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
