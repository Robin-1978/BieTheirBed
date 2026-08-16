"""Ticket-authenticated target Node resource invocation routes."""

from __future__ import annotations

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import (
    ResourceInvocationCancelRequest,
    ResourceInvocationRequest,
)


class RemoteResourceRoutes:
    async def _resource_invocation(self, request: Request) -> JSONResponse:
        invocation_id = self._path_identifier(request, "invocation_id")
        if invocation_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        body = await request.body()
        if len(body) > 8 * 1024 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            if request.method == "DELETE":
                parsed = ResourceInvocationCancelRequest.model_validate_json(body)
                accepted = await self._remote_models.cancel(
                    invocation_id, parsed.ticket
                )
                return JSONResponse({"cancel_requested": accepted})
            parsed = ResourceInvocationRequest.model_validate_json(body)
            chunks = await self._remote_models.invoke(
                invocation_id,
                parsed.ticket,
                parsed.request,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=403)
        return JSONResponse(
            {"chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
        )


__all__ = ["RemoteResourceRoutes"]
