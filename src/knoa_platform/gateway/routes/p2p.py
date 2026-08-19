"""Authenticated WebRTC signaling routes for App and Node resource peers."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import P2POfferRequest, ResourceP2POfferRequest


class P2PRoutes:
    async def _p2p_offer(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._body(
            request,
            P2POfferRequest,
            limit=30,
            max_body_bytes=2_100_000,
        )
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            answer = await self._p2p.create_answer(sdp=parsed.sdp, kind="app")
        except Exception:
            return JSONResponse({"error": "p2p_unavailable"}, status_code=503)
        return JSONResponse({"answer": answer})

    async def _resource_p2p_offer(self, request: Request) -> JSONResponse:
        parsed = await self._parse_body(
            request,
            ResourceP2POfferRequest,
            max_body_bytes=2_100_000,
        )
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            self._remote_models.authorize(parsed.ticket, parsed.invocation_id)
            answer = await self._p2p.create_answer(sdp=parsed.sdp, kind="resource")
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "p2p_unavailable"}, status_code=503)
        return JSONResponse({"answer": answer})


__all__ = ["P2PRoutes"]
