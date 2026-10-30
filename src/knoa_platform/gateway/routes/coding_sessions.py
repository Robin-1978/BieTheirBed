"""Authenticated mobile routes for external Codex and OpenCode sessions."""

from __future__ import annotations

import logging

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_codex_agent.coding_sessions import (
    CodingSessionConflict,
    CodingSessionNotFound,
    CodingSessionProviderError,
)
from knoa_platform.coding_sessions import CodingSessionListResult
from knoa_platform.gateway.protocol import (
    CodingSessionAttachRequest,
    CodingSessionContinueRequest,
    CodingSessionInterruptRequest,
    CodingSessionListQuery,
)

logger = logging.getLogger(__name__)


class CodingSessionRoutes:
    @staticmethod
    def _coding_session_error(exc: Exception) -> JSONResponse:
        if isinstance(exc, CodingSessionNotFound):
            return JSONResponse({"error": "not_found"}, status_code=404)
        if isinstance(exc, (CodingSessionConflict, LookupError)):
            return JSONResponse({"error": "conflict"}, status_code=409)
        if isinstance(exc, PermissionError):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if isinstance(exc, CodingSessionProviderError):
            logger.warning("Coding session provider failed", exc_info=exc)
            return JSONResponse({"error": "unavailable"}, status_code=503)
        logger.exception("Coding session route failed")
        return JSONResponse({"error": "unavailable"}, status_code=503)

    @staticmethod
    def _summary(item) -> dict:
        return {
            "provider": item.provider,
            "external_session_id": item.external_session_id,
            "title": item.title,
            "directory": item.directory,
            "state": item.state,
            "updated_at": item.updated_at,
            "latest_user_message": item.latest_user_message,
            "latest_assistant_message": item.latest_assistant_message,
            "read_only": item.read_only,
            "capabilities": list(item.capabilities),
            "native_state": item.native_state,
            "revision": item.revision,
        }

    @classmethod
    def _list_payload(cls, result: CodingSessionListResult) -> dict:
        return {
            "sessions": [cls._summary(item) for item in result.sessions],
            "next_cursor": result.next_cursor,
            "provider_errors": [
                {"provider": provider, "error": message}
                for provider, message in result.provider_errors
            ],
        }

    @classmethod
    def _detail_payload(cls, detail) -> dict:
        return {
            "session": cls._summary(detail.session),
            "messages": [
                {
                    "message_id": message.message_id,
                    "role": message.role,
                    "text": message.text,
                    "created_at": message.created_at,
                }
                for message in detail.messages
            ],
        }

    async def _list_coding_sessions(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = CodingSessionListQuery.model_validate(dict(request.query_params))
            result = await self._coding_sessions.list_sessions(
                provider=query.provider,
                directory=query.directory,
                search=query.search,
                limit=query.limit,
                cursor=query.cursor,
                include_archived=query.include_archived,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._coding_session_error(exc)
        return JSONResponse(self._list_payload(result))

    async def _get_coding_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        provider = self._path_identifier(request, "provider")
        external_id = self._path_identifier(request, "external_session_id")
        if provider is None or external_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            detail = await self._coding_sessions.get_session(provider, external_id)
        except Exception as exc:
            return self._coding_session_error(exc)
        return JSONResponse(self._detail_payload(detail))

    async def _attach_coding_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        provider = self._path_identifier(request, "provider")
        external_id = self._path_identifier(request, "external_session_id")
        if provider is None or external_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, CodingSessionAttachRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            binding = await self._coding_sessions.attach(
                principal_id=authenticated.device.principal_id,
                session_handle=parsed.session_handle,
                provider=provider,
                external_session_id=external_id,
            )
        except Exception as exc:
            return self._coding_session_error(exc)
        return JSONResponse(
            {
                "binding": {
                    "binding_id": binding.binding_id,
                    "session_handle": binding.session_handle,
                    "provider": binding.provider,
                    "external_session_id": binding.external_session_id,
                    "directory": binding.directory,
                    "created_at": binding.created_at,
                    "updated_at": binding.updated_at,
                }
            },
            status_code=201,
        )

    async def _continue_coding_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        provider = self._path_identifier(request, "provider")
        external_id = self._path_identifier(request, "external_session_id")
        if provider is None or external_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, CodingSessionContinueRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            receipt = await self._coding_sessions.continue_session(
                principal_id=authenticated.device.principal_id,
                provider=provider,
                external_session_id=external_id,
                text=parsed.text,
            )
        except Exception as exc:
            return self._coding_session_error(exc)
        return JSONResponse(
            {
                "external_session_id": receipt.external_session_id,
                "external_turn_id": receipt.external_turn_id,
                "accepted": receipt.accepted,
                "read_only": receipt.read_only,
            },
            status_code=202,
        )

    async def _interrupt_coding_session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        provider = self._path_identifier(request, "provider")
        external_id = self._path_identifier(request, "external_session_id")
        if provider is None or external_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, CodingSessionInterruptRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            interrupted = await self._coding_sessions.interrupt_session(
                principal_id=authenticated.device.principal_id,
                provider=provider,
                external_session_id=external_id,
                external_turn_id=parsed.external_turn_id,
            )
        except Exception as exc:
            return self._coding_session_error(exc)
        return JSONResponse({"interrupted": interrupted})
