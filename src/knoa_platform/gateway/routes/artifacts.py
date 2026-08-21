"""Fail-closed HTTP/TLS surface for Secure Gateway mobile access."""
from __future__ import annotations

import base64
import logging

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from knoa_platform.gateway.protocol import (
    ArtifactDownloadQuery,
    ArtifactSearchQuery,
    ArtifactUploadQuery,
    RuntimeQuery,
)

logger = logging.getLogger(__name__)
_MAX_BODY_BYTES = 16 * 1024



class ArtifactRoutes:

    async def _search_artifacts(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ArtifactSearchQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            artifacts = await self._core.search_artifacts(
                authenticated.device.principal_id,
                query.session_handle,
                query=query.q,
                kind=query.kind,
                limit=query.limit,
            )
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse(
            {"artifacts": list(artifacts), "next_cursor": ""},
            headers={"Cache-Control": "no-store"},
        )

    async def _transcribe_artifact(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        artifact_id = self._path_identifier(request, "artifact_id")
        if artifact_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = RuntimeQuery.model_validate(dict(request.query_params))
            result = await self._core.transcribe_artifact(
                authenticated.device.principal_id,
                query.session_handle,
                artifact_id,
            )
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except Exception as exc:
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})

    async def _upload_artifact(self, request: Request) -> JSONResponse:
        authenticated = self._authorize(request, limit=20)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            query = ArtifactUploadQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        media_type = request.headers.get("Content-Type", "").partition(";")[0].strip()
        if not self._valid_media_type(media_type):
            return JSONResponse({"error": "unsupported_media_type"}, status_code=415)
        declared_length = request.headers.get("Content-Length", "").strip()
        if declared_length:
            if not declared_length.isdecimal():
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if int(declared_length) > self._config.gateway_artifact_max_bytes:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > self._config.gateway_artifact_max_bytes:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if not body:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        token = self._bearer_token(request)
        renewed = self._authenticate_token(token)
        if renewed is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data_url = (
            f"data:{media_type};base64,"
            + base64.b64encode(body).decode("ascii")
        )
        try:
            artifact = await self._core.upload_artifact(
                renewed.device.principal_id,
                query.session_handle,
                data_url,
                media_type=media_type,
                name=query.name,
                caption=query.caption,
            )
        except Exception as exc:
            return self._core_error(exc)
        self._record_audit(
            "artifact_uploaded",
            request=request,
            device_id=renewed.device.device_id,
            principal_id=renewed.device.principal_id,
            detail_code=artifact.artifact_id,
        )
        return JSONResponse(
            {"artifact": artifact.model_dump(mode="json")},
            status_code=201,
        )

    async def _download_artifact(self, request: Request) -> JSONResponse | Response:
        authenticated = self._authorize(request, limit=60)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        artifact_id = self._path_identifier(request, "artifact_id")
        if artifact_id is None:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            query = ArtifactDownloadQuery.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            result = await self._core.download_artifact(
                authenticated.device.principal_id,
                query.session_handle,
                artifact_id,
            )
            data = self._decode_artifact_data_url(result.data_url)
        except ValueError:
            logger.warning("Secure Gateway received invalid Artifact data from Core")
            return JSONResponse({"error": "unavailable"}, status_code=503)
        except Exception as exc:
            return self._core_error(exc)
        if len(data) > self._config.gateway_artifact_max_bytes:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        if self._authenticate_token(self._bearer_token(request)) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return Response(
            data,
            media_type=result.artifact.media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": self._content_disposition(result.artifact.name),
                "X-Knoa-Artifact-Id": result.artifact.artifact_id,
            },
        )
