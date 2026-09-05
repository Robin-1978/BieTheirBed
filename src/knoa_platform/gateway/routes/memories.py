"""Fail-closed HTTP/TLS surface for Secure Gateway memory access."""
from __future__ import annotations

import logging
from starlette.requests import Request
from starlette.responses import JSONResponse

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
