"""Principal-scoped, explicitly allow-listed Secure Gateway bridge to Core."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from knoa_platform.agent_runtime.contracts import (
    ArtifactDownloadResult,
    ArtifactTranscriptionResult,
    MCPResourceCatalogResult,
    RuntimeStatus,
    ToolListResult,
)
from knoa_platform.artifacts import ArtifactRef, ArtifactStore
from knoa_platform.config import AppConfig
from knoa_platform.agents.definitions import ResolvedInvocationPolicy
from knoa_platform.configuration import (
    ConfigControlState,
    ConfigDraft,
    ConfigPublishResult,
    ConfigRevision,
    ConfigValidationResult,
    ManagedConfig,
)
from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.core_api import (
    ApprovalResolvedMessage,
    ArtifactInputRef,
    ChatApprovalResolvedMessage,
    ChatTurnSnapshot,
    ConversationSessionSnapshot,
    ProductTaskExecutionSnapshot,
    ProductTaskMessage,
    ProductTaskSnapshot,
    TaskAcceptedMessage,
    TaskCancelResultMessage,
    TaskListMessage,
    TaskPauseResultMessage,
    TaskResumedMessage,
    TaskSnapshot,
    HumanInteractionResolvedMessage,
)
from knoa_platform.service.core_client import CoreClient
from knoa_platform.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from knoa_platform.tasks import (
    PrincipalTaskEvent,
    TaskDefinitionState,
    TaskEvent,
    TaskLaunchPolicy,
    TaskOrigin,
    TaskState,
)


class GatewayCoreClient(Protocol):
    is_connected: bool

    async def create_session(
        self,
        *,
        activate: bool = True,
        agent_id: str | None = None,
    ) -> str: ...

    async def get_conversation_session(self, session_handle: str) -> ConversationSessionSnapshot: ...

    async def list_conversation_sessions(self, *, include_archived: bool = False, limit: int = 100, cursor: str = "") -> tuple[tuple[ConversationSessionSnapshot, ...], str]: ...

    async def update_conversation_session(self, session_handle: str, *, title: str | None = None, state=None, expected_revision: int | None = None) -> ConversationSessionSnapshot: ...

    async def delete_conversation_session(self, session_handle: str) -> None: ...

    async def create_task(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        client_request_id: str,
        tools_enabled: bool = True,
        priority: int = 0,
        parent_task_id: str = "",
        origin: TaskOrigin = TaskOrigin.USER,
        agent_id: str | None = None,
    ) -> TaskAcceptedMessage: ...

    async def create_chat_turn(
        self,
        session_handle: str,
        user_input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        client_request_id: str,
        tools_enabled: bool = True,
        agent_id: str | None = None,
    ) -> ChatTurnSnapshot: ...

    async def get_chat_turn(self, turn_id: str) -> ChatTurnSnapshot: ...

    async def list_chat_turns(
        self,
        session_handle: str,
        *,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[tuple[ChatTurnSnapshot, ...], str]: ...

    def chat_turn_updates(
        self,
        turn_id: str,
    ) -> AsyncIterator[ChatTurnSnapshot]: ...

    async def cancel_chat_turn(self, turn_id: str) -> ChatTurnSnapshot: ...

    async def retry_chat_turn(self, turn_id: str) -> ChatTurnSnapshot: ...

    async def resolve_chat_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> ChatApprovalResolvedMessage: ...

    async def resolve_interaction(
        self,
        interaction_id: str,
        value: object,
    ) -> HumanInteractionResolvedMessage: ...

    async def get_task(self, task_id: str) -> TaskSnapshot: ...

    async def list_tasks(
        self,
        *,
        session_handle: str = "",
        state: TaskState | None = None,
        origins: tuple[TaskOrigin, ...] = (),
        limit: int = 50,
        cursor: str = "",
    ) -> TaskListMessage: ...

    async def create_product_task(
        self,
        session_handle: str,
        goal: str,
        *,
        client_request_id: str,
        title: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
        agent_id: str | None = None,
    ) -> ProductTaskMessage: ...

    async def get_product_task(self, task_id: str) -> ProductTaskSnapshot: ...

    async def list_product_tasks(
        self,
        *,
        state: TaskDefinitionState | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[ProductTaskSnapshot, ...]: ...

    async def update_product_task(self, task_id: str, **changes) -> ProductTaskSnapshot: ...

    async def set_product_task_state(
        self, task_id: str, state: TaskDefinitionState
    ) -> ProductTaskSnapshot: ...

    async def delete_product_task(self, task_id: str) -> None: ...
    async def execute_product_task(
        self, task_id: str, *, launch_reason: str = "manual"
    ) -> ProductTaskExecutionSnapshot: ...
    async def get_product_task_execution(self, execution_id: str) -> ProductTaskExecutionSnapshot: ...
    async def list_product_task_executions(
        self, task_id: str, *, limit: int = 100
    ) -> tuple[ProductTaskExecutionSnapshot, ...]: ...
    async def delete_product_task_execution(self, execution_id: str) -> None: ...
    async def rerun_product_task_execution(self, execution_id: str) -> ProductTaskExecutionSnapshot: ...
    async def continue_product_task(
        self,
        task_id: str,
        input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        client_request_id: str,
    ) -> ProductTaskExecutionSnapshot: ...

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

    async def list_mcp_resources(self) -> MCPResourceCatalogResult: ...

    async def get_config_current(self) -> tuple[ConfigRevision, ConfigControlState, tuple[dict, ...]]: ...
    async def get_config_history(self, *, limit: int = 50) -> tuple[ConfigRevision, ...]: ...
    async def get_config_revision(self, revision_id: str) -> ConfigRevision: ...
    async def create_config_draft(self) -> ConfigDraft: ...
    async def get_config_draft(self, draft_id: str) -> ConfigDraft: ...
    async def replace_config_draft(self, draft_id: str, document: ManagedConfig, *, expected_version: int) -> ConfigDraft: ...
    async def validate_config_draft(self, draft_id: str, *, preflight: bool = False) -> ConfigValidationResult: ...
    async def publish_config_draft(self, draft_id: str, *, expected_version: int, summary: str = "") -> ConfigPublishResult: ...
    async def rollback_config(self, revision_id: str, *, summary: str = "") -> ConfigPublishResult: ...
    async def get_config_diff(self, from_revision_id: str, to_revision_id: str) -> tuple[dict, ...]: ...
    async def preview_invocation_policy(self, agent_id: str, *, invocation_kind: str = "user", caller_id: str = "", requested_tools: frozenset[str] | None = None, requested_skills: frozenset[str] | None = None) -> ResolvedInvocationPolicy: ...

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

    async def search_artifacts(
        self,
        session_handle: str,
        *,
        query: str = "",
        kind: str = "",
        limit: int = 50,
    ) -> tuple[dict, ...]: ...

    def principal_task_events(
        self,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[PrincipalTaskEvent]: ...

    def task_events(
        self,
        task_id: str,
        *,
        after_seq: int = 0,
    ) -> AsyncIterator[TaskEvent]: ...

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
        # Metadata search is read-only and uses the same registry database as
        # the runtime ArtifactStore.  Bytes remain behind Core's authenticated
        # download command.
        self._artifact_store = ArtifactStore(
            self._paths.attachments,
            persistent_root=self._paths.artifacts,
            db_path=self._paths.data / "assistant.db",
            ttl_seconds=config.attachment_ttl_seconds,
        )

    async def close(self) -> None:
        clients, self._clients = tuple(self._clients.values()), {}
        await asyncio.gather(
            *(client.disconnect() for client in clients),
            return_exceptions=True,
        )

    async def create_session(
        self,
        principal_id: str,
        *,
        activate: bool = True,
        agent_id: str | None = None,
    ) -> str:
        client = await self._client_for(principal_id)
        if activate:
            return await client.create_session(agent_id=agent_id)
        return await client.create_session(activate=False, agent_id=agent_id)

    async def get_conversation_session(self, principal_id: str, session_handle: str) -> ConversationSessionSnapshot:
        return await (await self._client_for(principal_id)).get_conversation_session(session_handle)

    async def list_conversation_sessions(self, principal_id: str, *, include_archived: bool = False, limit: int = 100, cursor: str = "") -> tuple[tuple[ConversationSessionSnapshot, ...], str]:
        return await (await self._client_for(principal_id)).list_conversation_sessions(
            include_archived=include_archived,
            limit=limit,
            cursor=cursor,
        )

    async def update_conversation_session(self, principal_id: str, session_handle: str, *, title: str | None = None, state=None, expected_revision: int | None = None) -> ConversationSessionSnapshot:
        return await (await self._client_for(principal_id)).update_conversation_session(
            session_handle,
            title=title,
            state=state,
            expected_revision=expected_revision,
        )

    async def delete_conversation_session(self, principal_id: str, session_handle: str) -> None:
        await (await self._client_for(principal_id)).delete_conversation_session(session_handle)

    async def create_task(
        self,
        principal_id: str,
        session_handle: str,
        user_input: str,
        attachments: tuple[ArtifactInputRef, ...],
        *,
        client_request_id: str,
        tools_enabled: bool,
        priority: int,
        parent_task_id: str,
        origin: TaskOrigin,
        agent_id: str | None = None,
    ) -> TaskAcceptedMessage:
        client = await self._client_for(principal_id)
        kwargs = dict(
            tools_enabled=tools_enabled,
            priority=priority,
            parent_task_id=parent_task_id,
        )
        kwargs["origin"] = origin
        kwargs["agent_id"] = agent_id
        return await client.create_task(
            session_handle,
            user_input,
            attachments,
            client_request_id=client_request_id,
            **kwargs,
        )

    async def create_chat_turn(
        self,
        principal_id: str,
        session_handle: str,
        user_input: str,
        attachments: tuple[ArtifactInputRef, ...],
        *,
        client_request_id: str,
        tools_enabled: bool,
        agent_id: str | None = None,
    ) -> ChatTurnSnapshot:
        return await (await self._client_for(principal_id)).create_chat_turn(
            session_handle,
            user_input,
            attachments,
            client_request_id=client_request_id,
            tools_enabled=tools_enabled,
            agent_id=agent_id,
        )

    async def get_chat_turn(
        self,
        principal_id: str,
        turn_id: str,
    ) -> ChatTurnSnapshot:
        return await (await self._client_for(principal_id)).get_chat_turn(turn_id)

    async def list_chat_turns(
        self,
        principal_id: str,
        session_handle: str,
        *,
        limit: int = 100,
        cursor: str = "",
    ) -> tuple[tuple[ChatTurnSnapshot, ...], str]:
        return await (await self._client_for(principal_id)).list_chat_turns(
            session_handle,
            limit=limit,
            cursor=cursor,
        )

    async def chat_turn_updates(
        self,
        principal_id: str,
        turn_id: str,
    ) -> AsyncIterator[ChatTurnSnapshot]:
        client = await self._client_for(principal_id)
        async for snapshot in client.chat_turn_updates(turn_id):
            yield snapshot

    async def cancel_chat_turn(
        self,
        principal_id: str,
        turn_id: str,
    ) -> ChatTurnSnapshot:
        return await (await self._client_for(principal_id)).cancel_chat_turn(turn_id)

    async def retry_chat_turn(
        self,
        principal_id: str,
        turn_id: str,
    ) -> ChatTurnSnapshot:
        return await (await self._client_for(principal_id)).retry_chat_turn(turn_id)

    async def resolve_chat_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> ChatApprovalResolvedMessage:
        return await (await self._client_for(principal_id)).resolve_chat_approval(
            approval_id,
            approved=approved,
        )

    async def resolve_interaction(
        self,
        principal_id: str,
        interaction_id: str,
        value: object,
    ) -> HumanInteractionResolvedMessage:
        return await (await self._client_for(principal_id)).resolve_interaction(
            interaction_id,
            value,
        )

    async def get_task(self, principal_id: str, task_id: str) -> TaskSnapshot:
        return await (await self._client_for(principal_id)).get_task(task_id)

    async def list_tasks(
        self,
        principal_id: str,
        *,
        session_handle: str,
        state: TaskState | None,
        origins: tuple[TaskOrigin, ...],
        limit: int,
        cursor: str,
    ) -> TaskListMessage:
        client = await self._client_for(principal_id)
        kwargs = dict(
            session_handle=session_handle,
            state=state,
            limit=limit,
            cursor=cursor,
        )
        if origins:
            kwargs["origins"] = origins
        return await client.list_tasks(**kwargs)

    async def create_product_task(
        self,
        principal_id: str,
        session_handle: str,
        goal: str,
        *,
        client_request_id: str,
        title: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        tools_enabled: bool = True,
        priority: int = 0,
        launch_policy: TaskLaunchPolicy | None = None,
        notification_policy: dict[str, bool] | None = None,
        agent_id: str | None = None,
        auto_launch: bool = True,
    ) -> ProductTaskMessage:
        return await (await self._client_for(principal_id)).create_product_task(
            session_handle,
            goal,
            client_request_id=client_request_id,
            title=title,
            attachments=attachments,
            tools_enabled=tools_enabled,
            priority=priority,
            launch_policy=launch_policy,
            notification_policy=notification_policy,
            agent_id=agent_id,
            auto_launch=auto_launch,
        )

    async def get_product_task(
        self,
        principal_id: str,
        task_id: str,
    ) -> ProductTaskSnapshot:
        return await (await self._client_for(principal_id)).get_product_task(task_id)

    async def list_product_tasks(
        self,
        principal_id: str,
        *,
        state: TaskDefinitionState | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[ProductTaskSnapshot, ...]:
        return await (await self._client_for(principal_id)).list_product_tasks(
            state=state,
            include_archived=include_archived,
            limit=limit,
        )

    async def update_product_task(
        self,
        principal_id: str,
        task_id: str,
        **changes,
    ) -> ProductTaskSnapshot:
        return await (await self._client_for(principal_id)).update_product_task(
            task_id,
            **changes,
        )

    async def set_product_task_state(
        self,
        principal_id: str,
        task_id: str,
        state: TaskDefinitionState,
    ) -> ProductTaskSnapshot:
        return await (await self._client_for(principal_id)).set_product_task_state(
            task_id,
            state,
        )

    async def delete_product_task(self, principal_id: str, task_id: str) -> None:
        await (await self._client_for(principal_id)).delete_product_task(task_id)

    async def execute_product_task(
        self,
        principal_id: str,
        task_id: str,
        *,
        launch_reason: str = "manual",
    ) -> ProductTaskExecutionSnapshot:
        return await (
            await self._client_for(principal_id)
        ).execute_product_task(task_id, launch_reason=launch_reason)

    async def get_product_task_execution(
        self,
        principal_id: str,
        execution_id: str,
    ) -> ProductTaskExecutionSnapshot:
        return await (
            await self._client_for(principal_id)
        ).get_product_task_execution(execution_id)

    async def list_product_task_executions(
        self,
        principal_id: str,
        task_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ProductTaskExecutionSnapshot, ...]:
        return await (
            await self._client_for(principal_id)
        ).list_product_task_executions(task_id, limit=limit)

    async def delete_product_task_execution(
        self,
        principal_id: str,
        execution_id: str,
    ) -> None:
        await (
            await self._client_for(principal_id)
        ).delete_product_task_execution(execution_id)

    async def rerun_product_task_execution(
        self,
        principal_id: str,
        execution_id: str,
    ) -> ProductTaskExecutionSnapshot:
        return await (
            await self._client_for(principal_id)
        ).rerun_product_task_execution(execution_id)

    async def continue_product_task(
        self,
        principal_id: str,
        task_id: str,
        input: str = "",
        attachments: tuple[ArtifactInputRef, ...] = (),
        *,
        client_request_id: str,
    ) -> ProductTaskExecutionSnapshot:
        return await (await self._client_for(principal_id)).continue_product_task(
            task_id,
            input,
            attachments,
            client_request_id=client_request_id,
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
        session_handle = await client.create_session(activate=False)
        return await client.create_task(
            session_handle,
            goal,
            previous.attachments,
            tools_enabled=previous.tools_enabled,
            priority=previous.priority,
            parent_task_id=previous.task_id,
            origin=previous.origin,
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

    async def list_mcp_resources(
        self,
        principal_id: str,
    ) -> MCPResourceCatalogResult:
        return await (await self._client_for(principal_id)).list_mcp_resources()

    async def get_config_current(self, principal_id: str):
        return await (await self._client_for(principal_id)).get_config_current()

    async def get_config_history(self, principal_id: str, *, limit: int = 50):
        return await (await self._client_for(principal_id)).get_config_history(limit=limit)

    async def get_config_revision(self, principal_id: str, revision_id: str):
        return await (await self._client_for(principal_id)).get_config_revision(revision_id)

    async def create_config_draft(self, principal_id: str):
        return await (await self._client_for(principal_id)).create_config_draft()

    async def get_config_draft(self, principal_id: str, draft_id: str):
        return await (await self._client_for(principal_id)).get_config_draft(draft_id)

    async def replace_config_draft(
        self,
        principal_id: str,
        draft_id: str,
        document: ManagedConfig,
        *,
        expected_version: int,
    ):
        return await (await self._client_for(principal_id)).replace_config_draft(
            draft_id,
            document,
            expected_version=expected_version,
        )

    async def validate_config_draft(
        self,
        principal_id: str,
        draft_id: str,
        *,
        preflight: bool = False,
    ):
        return await (await self._client_for(principal_id)).validate_config_draft(
            draft_id,
            preflight=preflight,
        )

    async def publish_config_draft(
        self,
        principal_id: str,
        draft_id: str,
        *,
        expected_version: int,
        summary: str = "",
    ):
        return await (await self._client_for(principal_id)).publish_config_draft(
            draft_id,
            expected_version=expected_version,
            summary=summary,
        )

    async def rollback_config(
        self,
        principal_id: str,
        revision_id: str,
        *,
        summary: str = "",
    ):
        return await (await self._client_for(principal_id)).rollback_config(
            revision_id,
            summary=summary,
        )

    async def get_config_diff(
        self,
        principal_id: str,
        from_revision_id: str,
        to_revision_id: str,
    ):
        return await (await self._client_for(principal_id)).get_config_diff(
            from_revision_id,
            to_revision_id,
        )

    async def preview_invocation_policy(
        self,
        principal_id: str,
        agent_id: str,
        **kwargs,
    ):
        return await (await self._client_for(principal_id)).preview_invocation_policy(
            agent_id,
            **kwargs,
        )

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

    async def search_artifacts(
        self,
        principal_id: str,
        session_handle: str,
        *,
        query: str = "",
        kind: str = "",
        limit: int = 50,
    ) -> tuple[dict, ...]:
        # Principal ownership is represented by the authenticated Core client
        # session.  Verify the session exists before returning registry rows.
        await (await self._client_for(principal_id)).get_conversation_session(session_handle)
        return tuple(
            await asyncio.to_thread(
                self._artifact_store.search,
                session_handle,
                query=query,
                kind=kind,
                limit=limit,
            )
        )

    async def principal_task_events(
        self,
        principal_id: str,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[PrincipalTaskEvent]:
        client = await self._client_for(principal_id)
        async for event in client.principal_task_events(after_id=after_id):
            yield event

    async def task_events(
        self,
        principal_id: str,
        task_id: str,
        *,
        after_seq: int = 0,
    ) -> tuple[TaskEvent, ...]:
        client = await self._client_for(principal_id)
        return tuple(
            [
                event
                async for event in client.task_events(
                    task_id,
                    after_seq=after_seq,
                )
            ]
        )

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
