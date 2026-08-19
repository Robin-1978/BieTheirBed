"""Loopback-only Node Console routes."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import secrets
from io import BytesIO

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from knoa_platform.console_ui import node_console_html
from knoa_platform.configuration import ManagedConfig
from knoa_platform.gateway.pairing import GatewayPairingPayload
from knoa_platform.gateway.protocol import NodeHubEnrollmentRequest, WriteSecretRequest


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

    async def _console_lifecycle(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        try:
            body = await asyncio.to_thread(self._host_lifecycle.status)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def _console_lifecycle_action(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        raw = await request.body()
        if len(raw) > 16 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            payload = json.loads(raw)
            body = await asyncio.to_thread(self._host_lifecycle.action, payload)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_action"}, status_code=400)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def _console_lifecycle_bundle(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        name = request.path_params["name"]
        if not name.endswith(".zip"):
            return JSONResponse({"error": "invalid_bundle_name"}, status_code=400)
        try:
            destination = self._host_lifecycle.bundle_path(name)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        size = 0
        try:
            with temporary.open("xb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024 * 1024:
                        raise OverflowError
                    stream.write(chunk)
            if size == 0:
                raise ValueError("empty_bundle")
            os.replace(temporary, destination)
        except OverflowError:
            temporary.unlink(missing_ok=True)
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            return JSONResponse({"error": "bundle_upload_failed"}, status_code=400)
        return JSONResponse({"bundle_name": name, "size_bytes": size}, status_code=201)

    async def _console_config(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        try:
            revision, state, generations = await self._core.get_config_current(
                self._config.owner_principal_id
            )
        except Exception as error:
            return self._core_error(error)
        return JSONResponse(
            {
                "revision": revision.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
                "generations": generations,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _console_config_publish(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        raw = await request.body()
        if len(raw) > 1024 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            payload = json.loads(raw)
            document = ManagedConfig.model_validate(payload.get("document"))
            summary = str(payload.get("summary") or "Node Console configuration update")[:512]
            principal = self._config.owner_principal_id
            draft = await self._core.create_config_draft(principal)
            draft = await self._core.replace_config_draft(
                principal,
                draft.draft_id,
                document,
                expected_version=draft.draft_version,
            )
            validation = await self._core.validate_config_draft(
                principal,
                draft.draft_id,
                preflight=True,
            )
            if not validation.valid:
                return JSONResponse(
                    {"error": "preflight_failed", "validation": validation.model_dump(mode="json")},
                    status_code=422,
                )
            result = await self._core.publish_config_draft(
                principal,
                draft.draft_id,
                expected_version=draft.draft_version,
                summary=summary,
            )
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return JSONResponse({"error": "invalid_configuration"}, status_code=400)
        except Exception as error:
            return self._core_error(error)
        workspace_sync: dict = {}
        try:
            workspace_sync = await self._node_relay.sync_workspace_resources()
        except Exception as error:  # Local configuration remains applied.
            workspace_sync = {"error": type(error).__name__}
        return JSONResponse(
            {
                "result": result.model_dump(mode="json"),
                "workspace_sync": workspace_sync,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _console_workspace_resources(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        try:
            state = await self._node_relay.workspace_resource_state()
        except PermissionError:
            return JSONResponse({"error": "node_not_enrolled"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "hub_unavailable"}, status_code=503)
        return JSONResponse(state, headers={"Cache-Control": "no-store"})

    async def _console_secret(self, request: Request) -> JSONResponse:
        if (error := self._console_authorize(request)) is not None:
            return error
        reference = request.path_params["reference"]
        try:
            if request.method == "GET":
                status = await asyncio.to_thread(self._provider_secrets.status, reference)
            else:
                raw = await request.body()
                if len(raw) > 70_000:
                    return JSONResponse({"error": "payload_too_large"}, status_code=413)
                parsed = WriteSecretRequest.model_validate_json(raw)
                status = await asyncio.to_thread(
                    self._provider_secrets.put,
                    reference,
                    parsed.value,
                )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except (OSError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse(status, headers={"Cache-Control": "no-store"})

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
