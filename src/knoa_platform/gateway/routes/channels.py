"""Owner-only channel configuration routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import ConfigureDingTalkRequest


class ChannelRoutes:
    async def _dingtalk_channel(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        if self._channel_controller is None:
            return JSONResponse(
                {"error": "channel_control_unavailable"}, status_code=503
            )
        try:
            if request.method == "GET":
                status = self._channel_controller.dingtalk_status()
            else:
                parsed = await self._body(
                    request,
                    ConfigureDingTalkRequest,
                    limit=10,
                    max_body_bytes=70_000,
                )
                if isinstance(parsed, JSONResponse):
                    return parsed
                status = await self._channel_controller.configure_dingtalk(
                    **parsed.model_dump()
                )
        except ValueError:
            return JSONResponse({"error": "invalid_dingtalk_settings"}, status_code=422)
        except RuntimeError:
            return JSONResponse(
                {"error": "channel_reconfigure_failed"}, status_code=503
            )
        return JSONResponse({"channel": status}, headers={"Cache-Control": "no-store"})


__all__ = ["ChannelRoutes"]
