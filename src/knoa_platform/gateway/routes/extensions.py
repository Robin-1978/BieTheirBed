"""Owner-only Extension Center import and package inventory routes."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import (
    ImportLocalMCPRequest,
    ImportRemoteMCPRequest,
    ImportSkillRequest,
)


class ExtensionRoutes:
    async def _extension_packages(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            packages = await asyncio.to_thread(self._extension_imports.list_packages)
        except (LookupError, OSError, ValueError):
            return JSONResponse({"error": "package_store_invalid"}, status_code=422)
        return JSONResponse({"packages": [item.public_dict() for item in packages]})

    async def _extension_import_skill(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_body(request, ImportSkillRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            result = await self._extension_imports.import_skill(
                authenticated.device.principal_id,
                body.source_path,
            )
        except (LookupError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": "import_rejected", "detail": str(exc)[:1000]},
                status_code=422,
            )
        except Exception as exc:  # noqa: BLE001 - normalized through Gateway error contract
            return self._core_error(exc)
        return JSONResponse({"result": result.as_dict()}, status_code=201)

    async def _extension_import_local_mcp(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_body(request, ImportLocalMCPRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            result = await self._extension_imports.import_local_mcp(
                authenticated.device.principal_id,
                body.source_path,
                body.server_id,
            )
        except (LookupError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": "import_rejected", "detail": str(exc)[:1000]},
                status_code=422,
            )
        except Exception as exc:  # noqa: BLE001 - normalized through Gateway error contract
            return self._core_error(exc)
        return JSONResponse({"result": result.as_dict()}, status_code=201)

    async def _extension_import_remote_mcp(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_body(request, ImportRemoteMCPRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            result = await self._extension_imports.import_remote_mcp(
                authenticated.device.principal_id,
                body.server_id,
                body.url,
                allow_private_network=body.allow_private_network,
            )
        except (LookupError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": "import_rejected", "detail": str(exc)[:1000]},
                status_code=422,
            )
        except Exception as exc:  # noqa: BLE001 - normalized through Gateway error contract
            return self._core_error(exc)
        return JSONResponse({"result": result.as_dict()}, status_code=201)

    async def _authorized_body(self, request: Request, model):
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._body(request, model, limit=10)
        if isinstance(parsed, JSONResponse):
            return parsed
        return authenticated, parsed


__all__ = ["ExtensionRoutes"]
