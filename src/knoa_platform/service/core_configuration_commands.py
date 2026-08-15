"""Owner-scoped typed Configuration Control Plane commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from knoa_platform.configuration import ConfigurationService
from knoa_platform.service.core_api import (
    ConfigCurrentMessage,
    ConfigDiffMessage,
    ConfigDraftMessage,
    ConfigHistoryMessage,
    ConfigPublishedMessage,
    ConfigRevisionMessage,
    ConfigValidationMessage,
    CreateConfigDraftRequest,
    GetConfigCurrentRequest,
    GetConfigDiffRequest,
    GetConfigDraftRequest,
    GetConfigHistoryRequest,
    GetConfigRevisionRequest,
    InvocationPolicyPreviewMessage,
    PreflightConfigDraftRequest,
    PreviewInvocationPolicyRequest,
    PublishConfigDraftRequest,
    ReplaceConfigDraftRequest,
    RollbackConfigRequest,
    ValidateConfigDraftRequest,
)

Send = Callable[[Any], Awaitable[None]]
PolicyPreview = Callable[[str, PreviewInvocationPolicyRequest], Awaitable[Any]]


class ConfigurationCommandHandler:
    def __init__(
        self,
        configuration: ConfigurationService,
        *,
        owner_principal_id: str,
        generation_states: Callable[[], tuple[Any, ...]],
        policy_preview: PolicyPreview,
    ) -> None:
        self._configuration = configuration
        self._owner = owner_principal_id
        self._generation_states = generation_states
        self._policy_preview = policy_preview

    def _authorize(self, principal: str) -> None:
        if principal != self._owner:
            raise PermissionError("Configuration administration is owner-only")

    async def dispatch(self, principal: str, request: Any, send: Send) -> bool:
        managed_request = isinstance(
            request,
            (
                GetConfigCurrentRequest,
                GetConfigHistoryRequest,
                GetConfigRevisionRequest,
                CreateConfigDraftRequest,
                GetConfigDraftRequest,
                ReplaceConfigDraftRequest,
                ValidateConfigDraftRequest,
                PreflightConfigDraftRequest,
                PublishConfigDraftRequest,
                RollbackConfigRequest,
                GetConfigDiffRequest,
                PreviewInvocationPolicyRequest,
            ),
        )
        if not managed_request:
            return False
        self._authorize(principal)
        if isinstance(request, GetConfigCurrentRequest):
            await send(
                ConfigCurrentMessage(
                    request_id=request.request_id,
                    revision=self._configuration.current(),
                    state=self._configuration.state(),
                    generations=tuple(
                        {
                            "agent_id": item.agent_id,
                            "active_generation": item.active_generation,
                            "draining_generation": item.draining_generation,
                            "active_leases": item.active_leases,
                            "draining_leases": item.draining_leases,
                            "enabled": item.enabled,
                        }
                        for item in self._generation_states()
                    ),
                )
            )
        elif isinstance(request, GetConfigHistoryRequest):
            await send(
                ConfigHistoryMessage(
                    request_id=request.request_id,
                    revisions=self._configuration.history(limit=request.limit),
                )
            )
        elif isinstance(request, GetConfigRevisionRequest) and not isinstance(
            request, RollbackConfigRequest
        ):
            await send(
                ConfigRevisionMessage(
                    request_id=request.request_id,
                    revision=self._configuration.revision(request.revision_id),
                )
            )
        elif isinstance(request, CreateConfigDraftRequest):
            await send(
                ConfigDraftMessage(
                    request_id=request.request_id,
                    draft=self._configuration.create_draft(actor=principal),
                )
            )
        elif isinstance(request, ReplaceConfigDraftRequest):
            await send(
                ConfigDraftMessage(
                    request_id=request.request_id,
                    draft=self._configuration.replace_draft(
                        request.draft_id,
                        request.document,
                        expected_version=request.expected_version,
                        actor=principal,
                    ),
                )
            )
        elif isinstance(request, GetConfigDraftRequest) and not isinstance(
            request,
            (
                ValidateConfigDraftRequest,
                PreflightConfigDraftRequest,
                PublishConfigDraftRequest,
            ),
        ):
            await send(
                ConfigDraftMessage(
                    request_id=request.request_id,
                    draft=self._configuration.draft(request.draft_id),
                )
            )
        elif isinstance(request, ValidateConfigDraftRequest):
            await send(
                ConfigValidationMessage(
                    request_id=request.request_id,
                    result=await self._configuration.validate(request.draft_id),
                )
            )
        elif isinstance(request, PreflightConfigDraftRequest):
            await send(
                ConfigValidationMessage(
                    request_id=request.request_id,
                    result=await self._configuration.preflight(request.draft_id),
                )
            )
        elif isinstance(request, PublishConfigDraftRequest):
            await send(
                ConfigPublishedMessage(
                    request_id=request.request_id,
                    result=await self._configuration.publish(
                        request.draft_id,
                        expected_version=request.expected_version,
                        actor=principal,
                        summary=request.summary,
                    ),
                )
            )
        elif isinstance(request, RollbackConfigRequest):
            await send(
                ConfigPublishedMessage(
                    request_id=request.request_id,
                    result=await self._configuration.rollback(
                        request.revision_id,
                        actor=principal,
                        summary=request.summary,
                    ),
                )
            )
        elif isinstance(request, GetConfigDiffRequest):
            await send(
                ConfigDiffMessage(
                    request_id=request.request_id,
                    changes=self._configuration.diff(
                        request.from_revision_id,
                        request.to_revision_id,
                    ),
                )
            )
        elif isinstance(request, PreviewInvocationPolicyRequest):
            await send(
                InvocationPolicyPreviewMessage(
                    request_id=request.request_id,
                    policy=await self._policy_preview(principal, request),
                )
            )
        return True
