"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import stat
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.auth import (
    AuthenticatedGatewaySession,
    GatewayAuthenticationRejectedError,
)
from knoa_platform.gateway.protocol import (
    GatewayRequest,
)
from knoa_platform.private_files import IS_WINDOWS
from knoa_platform.service.core_client import (
    CoreConnectionLostError,
    CoreRequestError,
    CoreRequestTimeoutError,
)

logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024



class GatewayHttp:

    def _authorize(
        self,
        request: Request,
        *,
        limit: int,
    ) -> AuthenticatedGatewaySession | JSONResponse:
        token = self._bearer_token(request)
        if not token:
            self._record_audit("session_rejected", request=request)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session = self._authenticate_token(token)
        if session is None:
            self._record_audit("session_rejected", request=request)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = f"authorized:{request.url.path}:{session.device.device_id}"
        if not self._limiter.allow(key, limit=limit):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        self._record_audit(
            "command",
            request=request,
            device_id=session.device.device_id,
            principal_id=session.device.principal_id,
            detail_code=f"{request.method} {request.url.path}",
        )
        return session

    def _record_audit(
        self,
        event_type: str,
        *,
        request: Request,
        device_id: str = "",
        principal_id: str = "",
        detail_code: str = "",
    ) -> None:
        try:
            remote = request.client.host if request.client is not None else ""
            self._audit.append(
                event_type,
                device_id=device_id,
                principal_id=principal_id,
                remote_address=remote,
                detail_code=detail_code,
            )
        except Exception:
            logger.warning("Secure Gateway audit append failed", exc_info=True)

    def _authenticate_token(self, token: str) -> AuthenticatedGatewaySession | None:
        try:
            return self._authentication.authenticate_session(token)
        except GatewayAuthenticationRejectedError:
            return None

    @staticmethod
    def _bearer_token(request: Request) -> str:
        authorization = request.headers.get("Authorization", "")
        scheme, space, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not space or not token or " " in token:
            return ""
        return token

    async def _body(
        self,
        request: Request,
        model: type[GatewayRequest],
        *,
        limit: int,
        max_body_bytes: int = _MAX_BODY_BYTES,
    ) -> GatewayRequest | JSONResponse:
        host = request.client.host if request.client is not None else "unknown"
        key = f"{request.url.path}:{host}"
        if not self._limiter.allow(key, limit=limit):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await self._parse_body(
            request,
            model,
            max_body_bytes=max_body_bytes,
        )

    async def _parse_body(
        self,
        request: Request,
        model: type[GatewayRequest],
        *,
        max_body_bytes: int = _MAX_BODY_BYTES,
    ) -> GatewayRequest | JSONResponse:
        content_type = request.headers.get("Content-Type", "").partition(";")[0]
        if content_type.strip().lower() != "application/json":
            return JSONResponse({"error": "unsupported_media_type"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_body_bytes:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            return model.model_validate_json(bytes(body))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    @staticmethod
    def _path_identifier(request: Request, name: str) -> str | None:
        value = str(request.path_params.get(name, "")).strip()
        if not value or len(value) > 128:
            return None
        return value

    @staticmethod
    def _core_error(exc: Exception) -> JSONResponse:
        if isinstance(exc, CoreRequestError):
            if exc.code in {
                "task_not_found",
                "chat_turn_not_found",
                "session_not_found",
                "approval_not_found",
                "artifact_not_found",
                "config_not_found",
            }:
                return JSONResponse({"error": "not_found"}, status_code=404)
            if exc.code in {"invalid_request", "invalid_state"}:
                return JSONResponse({"error": "rejected"}, status_code=422)
            if exc.code == "config_conflict":
                return JSONResponse({"error": "conflict"}, status_code=409)
            if exc.code == "config_apply_failed":
                return JSONResponse({"error": "rejected"}, status_code=422)
            if exc.code == "capability_denied":
                return JSONResponse({"error": "forbidden"}, status_code=403)
            if exc.code == "artifact_too_large":
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if isinstance(
            exc,
            (CoreConnectionLostError, CoreRequestTimeoutError),
        ):
            return JSONResponse({"error": "unavailable"}, status_code=503)
        logger.warning("Secure Gateway Core request failed", exc_info=exc)
        return JSONResponse({"error": "unavailable"}, status_code=503)

    @staticmethod
    def _event_cursor(request: Request, query_after_id: int) -> int:
        header = request.headers.get("Last-Event-ID", "").strip()
        if not header:
            return query_after_id
        if not header.isascii() or not header.isdecimal():
            return query_after_id
        header_id = int(header)
        if header_id > 9_223_372_036_854_775_807:
            raise ValueError("invalid event cursor")
        # EventSource reconnects with Last-Event-ID while keeping the original
        # URL query string.  The header is the newer cursor when it advances;
        # never move a reconnect backwards if the URL still has a later value.
        return max(query_after_id, header_id)

    @staticmethod
    def _sse(event: str, payload: dict[str, Any], *, event_id: int | None = None) -> bytes:
        lines = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event}")
        lines.append(
            "data: "
            + json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return ("\n".join(lines) + "\n\n").encode("utf-8")

    @staticmethod
    def _valid_media_type(value: str) -> bool:
        return bool(
            0 < len(value) <= 128
            and re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", value)
        )

    @staticmethod
    def _decode_artifact_data_url(data_url: str) -> bytes:
        if not data_url.startswith("data:") or ";base64," not in data_url:
            raise ValueError("invalid Artifact data URL")
        _metadata, encoded = data_url.split(",", 1)
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid Artifact data URL") from exc

    @staticmethod
    def _content_disposition(name: str) -> str:
        from urllib.parse import quote

        encoded = quote(name or "artifact", safe="")
        return f"attachment; filename=artifact; filename*=UTF-8''{encoded}"

    @staticmethod
    def _tls_file(value: str, *, label: str, private: bool) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValueError(f"Secure Gateway TLS {label} path must be absolute")
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise ValueError(f"Secure Gateway TLS {label} is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                f"Secure Gateway TLS {label} must be a regular non-symlink file"
            )
        if not IS_WINDOWS:
            if metadata.st_uid != os.geteuid():
                raise ValueError(f"Secure Gateway TLS {label} has the wrong owner")
            if private and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError("Secure Gateway TLS private key must be owner-only")
        if metadata.st_size <= 0:
            raise ValueError(f"Secure Gateway TLS {label} is empty")
        return candidate.resolve()

    @staticmethod
    def _challenge_response(challenge: Any) -> JSONResponse:
        return JSONResponse(
            {
                "challenge_id": challenge.challenge_id,
                "nonce": challenge.nonce,
                "expires_at": challenge.expires_at,
            }
        )
