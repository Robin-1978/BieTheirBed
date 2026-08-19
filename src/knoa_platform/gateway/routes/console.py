"""Loopback-only Node Console routes."""

from __future__ import annotations

import hmac
import ipaddress
from io import BytesIO

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from knoa_platform.console_ui import node_console_html
from knoa_platform.gateway.pairing import GatewayPairingPayload
from knoa_platform.gateway.protocol import NodeHubEnrollmentRequest


class ConsoleRoutes:
    async def _console_page(self, request: Request) -> Response:
        if not self._console_local(request):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return HTMLResponse(
            node_console_html(self._console_csrf_token),
            headers=self._console_headers(),
        )

    async def _console_status(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        return JSONResponse(
            {
                "node": self._node_identity.descriptor(),
                "hub": self._node_relay.status,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _console_hub_enroll(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        try:
            raw = await request.body()
            if len(raw) > 16 * 1024:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
            parsed = NodeHubEnrollmentRequest.model_validate_json(raw)
            enrollment = await self._node_hub.enroll(parsed)
            await self._node_relay.restart()
        except ValidationError:
            return JSONResponse({"error": "invalid_enrollment_code"}, status_code=400)
        except PermissionError:
            return JSONResponse({"error": "enrollment_rejected"}, status_code=401)
        except Exception:
            return JSONResponse({"error": "hub_unavailable"}, status_code=503)
        return JSONResponse(
            {"enrollment": enrollment.__dict__, "relay_connected": False},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    async def _console_pairing(self, request: Request) -> Response:
        if (error := self._console_authorize(request)) is not None:
            return error
        enrollment = self._node_hub_store.load()
        if enrollment is None:
            return JSONResponse({"error": "node_not_enrolled"}, status_code=409)
        try:
            grant = self._identities.create_pairing_grant(
                self._config.owner_principal_id,
                ttl_seconds=300,
            )
            payload = GatewayPairingPayload.from_grant(
                grant,
                enrollment.hub_url,
                transport="relay",
                node_id=self._node_identity.node_id,
                node_signing_public_key=self._node_identity.signing_public_key,
                node_configuration_public_key=(
                    self._node_identity.configuration_public_key
                ),
            ).encoded()
            import qrcode

            code = qrcode.QRCode(border=2)
            code.add_data(payload)
            code.make(fit=True)
            stream = BytesIO()
            code.make_image(fill_color="black", back_color="white").save(
                stream,
                format="PNG",
            )
        except (LookupError, ValueError):
            return JSONResponse({"error": "pairing_unavailable"}, status_code=409)
        return Response(
            stream.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": "inline; filename=knoa-pairing.png",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def _console_authorize(self, request: Request) -> JSONResponse | None:
        if not self._console_local(request):
            return JSONResponse({"error": "not_found"}, status_code=404)
        supplied = request.headers.get("X-Knoa-Console", "")
        if not supplied or not hmac.compare_digest(
            supplied,
            self._console_csrf_token,
        ):
            return JSONResponse({"error": "console_csrf_rejected"}, status_code=403)
        return None

    @staticmethod
    def _console_local(request: Request) -> bool:
        if request.client is None:
            return False
        try:
            return ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _console_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }


__all__ = ["ConsoleRoutes"]
