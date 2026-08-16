"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from knoa_platform.gateway.auth import (
    GatewayAuthenticationRejectedError,
)
from knoa_platform.gateway.identity import (
    DeviceAlreadyPairedError,
    PairingGrantRejectedError,
)
from knoa_platform.gateway.protocol import (
    AuditQuery,
    AuthChallengeRequest,
    AuthCompleteRequest,
    PairChallengeRequest,
    PairCompleteRequest,
    RuntimeQuery,
)

logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024



class DeviceRoutes:

    async def _health(self, _request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "scope": "authentication",
                "node_id": self._node_identity.node_id,
            }
        )

    async def _node(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        return JSONResponse(self._node_identity.descriptor())

    async def _agents(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            revision, _state, _generations = await self._core.get_config_current(
                authenticated.device.principal_id
            )
        except Exception as exc:
            return self._core_error(exc)
        system = revision.document.agent_system
        return JSONResponse({
            "default_agent": system.default_agent,
            "agents": [
                {
                    "agent_id": agent_id,
                    "display_name": system.profiles[
                        definition.profile_id
                    ].display_name,
                }
                for agent_id, definition in system.agents.items()
                if definition.enabled
                and system.profiles[definition.profile_id].visibility == "user"
            ],
        })

    async def _openapi(self, _request: Request) -> JSONResponse:
        from knoa_platform.gateway.openapi import gateway_openapi_schema

        return JSONResponse(gateway_openapi_schema())

    async def _pair_challenge(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, PairChallengeRequest, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            challenge = self._authentication.begin_pairing(parsed.grant_id)
        except (GatewayAuthenticationRejectedError, PairingGrantRejectedError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return self._challenge_response(challenge)

    async def _pair_complete(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, PairCompleteRequest, limit=10)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            device = self._authentication.complete_pairing(**parsed.model_dump())
        except (
            GatewayAuthenticationRejectedError,
            PairingGrantRejectedError,
            DeviceAlreadyPairedError,
            ValueError,
        ):
            return JSONResponse({"error": "rejected"}, status_code=401)
        self._record_audit(
            "paired",
            request=request,
            device_id=device.device_id,
            principal_id=device.principal_id,
        )
        return JSONResponse(
            {
                "device_id": device.device_id,
                "principal_id": device.principal_id,
                "node": self._node_identity.descriptor(),
            },
            status_code=201,
        )

    async def _auth_challenge(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, AuthChallengeRequest, limit=30)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            challenge = self._authentication.begin_authentication(parsed.device_id)
        except GatewayAuthenticationRejectedError:
            return JSONResponse({"error": "rejected"}, status_code=401)
        return self._challenge_response(challenge)

    async def _auth_complete(self, request: Request) -> JSONResponse:
        parsed = await self._body(request, AuthCompleteRequest, limit=20)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            session = self._authentication.complete_authentication(
                **parsed.model_dump(),
                session_ttl_seconds=self._config.gateway_session_ttl_seconds,
            )
        except (GatewayAuthenticationRejectedError, ValueError):
            self._record_audit("session_rejected", request=request)
            return JSONResponse({"error": "rejected"}, status_code=401)
        self._record_audit(
            "authenticated",
            request=request,
            device_id=session.device_id,
            principal_id=session.principal_id,
        )
        return JSONResponse(
            {
                "token": session.token,
                "expires_at": session.expires_at,
                "device_id": session.device_id,
            }
        )

    async def _session(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        return JSONResponse(
            {
                "session_id": authenticated.session_id,
                "device_id": authenticated.device.device_id,
                "principal_id": authenticated.device.principal_id,
                "expires_at": authenticated.expires_at,
            }
        )

    async def _runtime_status(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = RuntimeQuery.model_validate(dict(request.query_params))
            result = await self._core.status(
                authenticated.device.principal_id,
                query.session_handle,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _list_tools(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = RuntimeQuery.model_validate(dict(request.query_params))
            result = await self._core.list_tools(
                authenticated.device.principal_id,
                query.session_handle,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _list_mcp_resources(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=120)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            result = await self._core.list_mcp_resources(
                authenticated.device.principal_id,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _latest_android_release(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=30)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            release = await asyncio.to_thread(self._releases.latest)
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        assert release is not None
        return JSONResponse(
            {
                "platform": "android",
                "channel": "personal",
                "version_name": release.version_name,
                "version_code": release.version_code,
                "min_supported_version_code": release.min_supported_version_code,
                "size_bytes": release.size_bytes,
                "sha256": release.sha256,
                "published_at": release.published_at,
                "release_notes": release.release_notes,
                "download_path": (
                    f"/releases/android/{release.version_code}/"
                    f"{release.sha256}/knoa.apk"
                ),
            }
        )

    async def _download_android_release(
        self, request: Request
    ) -> JSONResponse | FileResponse:
        raw_version_code = str(request.path_params.get("version_code", ""))
        requested_sha256 = str(request.path_params.get("sha256", "")).lower()
        if not raw_version_code.isascii() or not raw_version_code.isdecimal():
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if (
            len(requested_sha256) != 64
            or any(character not in "0123456789abcdef" for character in requested_sha256)
        ):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        version_code = int(raw_version_code)
        if version_code < 1 or version_code > 2_100_000_000:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            release = await asyncio.to_thread(self._releases.get, version_code)
            if release.sha256 != requested_sha256:
                raise LookupError
            package = await asyncio.to_thread(self._releases.package_path, release)
            metadata = await asyncio.to_thread(package.stat)
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(
            package,
            media_type="application/vnd.android.package-archive",
            filename=f"knoa-{release.version_name}.apk",
            stat_result=metadata,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": f'"{release.sha256}"',
                "X-Knoa-SHA256": release.sha256,
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _device_audit(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = AuditQuery.model_validate(dict(request.query_params))
            events = self._audit.list_for_device(
                authenticated.device.principal_id,
                authenticated.device.device_id,
                after_id=query.after_id,
                limit=query.limit,
            )
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse(
            {
                "events": [
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at,
                        "remote_address_hash": event.remote_address_hash,
                        "detail_code": event.detail_code,
                    }
                    for event in events
                ]
            }
        )

    async def _device(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=10)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        device = authenticated.device
        try:
            await asyncio.to_thread(
                self._authentication.revoke_device,
                device.principal_id,
                device.device_id,
            )
        except (ValueError, LookupError):
            return JSONResponse({"error": "not_found"}, status_code=404)
        self._record_audit(
            "device_revoked",
            request=request,
            device_id=device.device_id,
            principal_id=device.principal_id,
        )
        return JSONResponse({"revoked": True})
