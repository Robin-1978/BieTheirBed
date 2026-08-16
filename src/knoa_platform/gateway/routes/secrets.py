"""Write-only owner secret management routes."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import WriteSecretRequest


class SecretRoutes:
    async def _secret(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        reference = self._path_identifier(request, "reference")
        if reference is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if request.method == "GET":
            try:
                status = await asyncio.to_thread(self._provider_secrets.status, reference)
            except ValueError:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            return JSONResponse(status)
        parsed = await self._body(request, WriteSecretRequest, limit=20, max_body_bytes=70_000)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            status = await asyncio.to_thread(self._provider_secrets.put, reference, parsed.value)
        except (OSError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse(status)


__all__ = ["SecretRoutes"]
