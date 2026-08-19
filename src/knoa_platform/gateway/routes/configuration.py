"""Owner-only HTTP surface for the managed Configuration Control Plane."""

from __future__ import annotations

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import (
    ConfigDiffQuery,
    ConfigHistoryQuery,
    PreviewInvocationPolicyRequest,
    PublishConfigDraftRequest,
    ReplaceConfigDraftRequest,
    RollbackConfigRequest,
)

_CONFIG_BODY_BYTES = 1024 * 1024


class ConfigurationRoutes:
    def _authorize_configuration(self, request: Request):
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        if authenticated.device.principal_id != self._config.owner_principal_id:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return authenticated

    async def _config_current(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            revision, state, generations = await self._core.get_config_current(
                authenticated.device.principal_id
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {
                "revision": revision.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "generations": generations,
            }
        )

    async def _config_history(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ConfigHistoryQuery.model_validate(dict(request.query_params))
            revisions = await self._core.get_config_history(
                authenticated.device.principal_id,
                limit=query.limit,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"revisions": [item.model_dump(mode="json") for item in revisions]}
        )

    async def _config_revision(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        revision_id = self._path_identifier(request, "revision_id")
        if revision_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            revision = await self._core.get_config_revision(
                authenticated.device.principal_id,
                revision_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"revision": revision.model_dump(mode="json")})

    async def _config_drafts(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            draft = await self._core.create_config_draft(
                authenticated.device.principal_id
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"draft": draft.model_dump(mode="json")}, status_code=201)

    async def _config_draft(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        draft_id = self._path_identifier(request, "draft_id")
        if draft_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            if request.method == "GET":
                draft = await self._core.get_config_draft(
                    authenticated.device.principal_id,
                    draft_id,
                )
            else:
                parsed = await self._body(
                    request,
                    ReplaceConfigDraftRequest,
                    limit=60,
                    max_body_bytes=_CONFIG_BODY_BYTES,
                )
                if isinstance(parsed, JSONResponse):
                    return parsed
                draft = await self._core.replace_config_draft(
                    authenticated.device.principal_id,
                    draft_id,
                    parsed.document,
                    expected_version=parsed.expected_version,
                )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"draft": draft.model_dump(mode="json")})

    async def _config_validate(self, request: Request) -> JSONResponse:
        return await self._validate_config_draft(request, preflight=False)

    async def _config_preflight(self, request: Request) -> JSONResponse:
        return await self._validate_config_draft(request, preflight=True)

    async def _validate_config_draft(
        self,
        request: Request,
        *,
        preflight: bool,
    ) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        draft_id = self._path_identifier(request, "draft_id")
        if draft_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            result = await self._core.validate_config_draft(
                authenticated.device.principal_id,
                draft_id,
                preflight=preflight,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _config_publish(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        draft_id = self._path_identifier(request, "draft_id")
        if draft_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._body(request, PublishConfigDraftRequest, limit=30)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.publish_config_draft(
                authenticated.device.principal_id,
                draft_id,
                expected_version=parsed.expected_version,
                summary=parsed.summary,
            )
        except Exception as exc:
            return self._core_error(exc)
        workspace_sync: dict = {}
        try:
            workspace_sync = await self._node_relay.sync_workspace_resources()
        except Exception as exc:  # Local configuration remains authoritative.
            workspace_sync = {"error": type(exc).__name__}
        return JSONResponse(
            {
                "result": result.model_dump(mode="json"),
                "workspace_sync": workspace_sync,
            }
        )

    async def _config_rollback(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._body(request, RollbackConfigRequest, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._core.rollback_config(
                authenticated.device.principal_id,
                parsed.revision_id,
                summary=parsed.summary,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _config_diff(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ConfigDiffQuery.model_validate(dict(request.query_params))
            changes = await self._core.get_config_diff(
                authenticated.device.principal_id,
                query.from_revision_id,
                query.to_revision_id,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"changes": changes})

    async def _config_policy_preview(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._body(request, PreviewInvocationPolicyRequest, limit=60)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            policy = await self._core.preview_invocation_policy(
                authenticated.device.principal_id,
                parsed.agent_id,
                invocation_kind=parsed.invocation_kind,
                caller_id=parsed.caller_id,
                requested_tools=parsed.requested_tools,
                requested_skills=parsed.requested_skills,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"policy": policy.model_dump(mode="json")})
