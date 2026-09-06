"""Fail-closed HTTP/TLS surface for Secure Gateway memory access."""
from __future__ import annotations

import logging
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.agent_runtime.contracts import MemoryRecord
from knoa_platform.gateway.protocol import MemorySavedResponse, MemoryUpsertRequest

logger = logging.getLogger(__name__)


class MemoriesRouteMixin:
    """Routes for querying and managing Scoped Memories."""

    async def _list_memories(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            result = await self._core.list_memory(
                authenticated.device.principal_id,
            )
        except Exception as exc:
            return self._core_error(exc)

        items = [m.model_dump(mode="json") for m in result.memories]
        category = request.query_params.get("category")
        importance = request.query_params.get("importance")
        if category:
            items = [m for m in items if m.get("category") == category]
        if importance:
            items = [m for m in items if m.get("importance") == importance]

        return JSONResponse({"items": items, "total": len(items)})

    async def _clear_memories(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            result = await self._core.clear_memory(
                authenticated.device.principal_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"cleared": result.cleared})

    async def _delete_memory(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        key = self._path_identifier(request, "key")
        if not key:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            result = await self._core.delete_memory(
                authenticated.device.principal_id,
                key=key,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"deleted": result.deleted})

    async def _save_memory(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse_body(request, MemoryUpsertRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            record = MemoryRecord(
                key=parsed.key,
                value=parsed.value,
                category=parsed.category,
                importance=parsed.importance,
                confidence=parsed.confidence,
                source="manual",
            )
            result = await self._core.set_memory(
                authenticated.device.principal_id,
                record=record,
            )
        except ValueError as exc:
            return JSONResponse({"error": "invalid_request", "message": str(exc)}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(MemorySavedResponse(key=result.key, saved=result.saved).model_dump(mode="json"), status_code=201)

    async def _update_memory(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        key = self._path_identifier(request, "key")
        if not key:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        parsed = await self._parse_body(request, MemoryUpsertRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            record = MemoryRecord(
                key=key,
                value=parsed.value,
                category=parsed.category,
                importance=parsed.importance,
                confidence=parsed.confidence,
                source="manual",
            )
            result = await self._core.set_memory(
                authenticated.device.principal_id,
                record=record,
            )
        except ValueError as exc:
            return JSONResponse({"error": "invalid_request", "message": str(exc)}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(MemorySavedResponse(key=result.key, saved=result.saved).model_dump(mode="json"))
