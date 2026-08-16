"""Owner-only Node endpoint for sealed Fleet candidates."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import ApplyFleetCandidateRequest


class FleetRoutes:
    async def _fleet_apply(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._body(request, ApplyFleetCandidateRequest, limit=10, max_body_bytes=1024 * 1024)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            result = await self._fleet_candidates.apply(
                authenticated.device.principal_id,
                parsed.rollout_id,
                parsed.envelope,
            )
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except RuntimeError as exc:
            if str(exc) == "revision_conflict":
                return JSONResponse({"error": "revision_conflict"}, status_code=409)
            return JSONResponse({"error": "rejected"}, status_code=422)
        except (LookupError, OSError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        except Exception as exc:  # noqa: BLE001 - normalized through Gateway error contract
            return self._core_error(exc)
        return JSONResponse({"result": result.model_dump(mode="json")})


__all__ = ["FleetRoutes"]
