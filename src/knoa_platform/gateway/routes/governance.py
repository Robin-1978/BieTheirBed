"""Owner-only improvement and signed Capability Catalog control planes."""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from knoa_platform.gateway.protocol import (
    ApproveImprovementCandidateRequest,
    CreateEvaluationCaseRequest,
    CreateImprovementCandidateRequest,
    FinishImprovementCanaryRequest,
    RecordImprovementEvidenceRequest,
    ReplayImprovementCandidateRequest,
    SelectCatalogCapabilityRequest,
)


class GovernanceRoutes:
    async def _catalog_entries(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            entries = self._capability_catalog.list_entries(authenticated.device.principal_id)
        except (LookupError, OSError, PermissionError, ValueError) as exc:
            return JSONResponse({"error": "catalog_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse({"entries": list(entries)})

    async def _catalog_select(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, SelectCatalogCapabilityRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            result = self._capability_catalog.select(
                authenticated.device.principal_id,
                str(request.path_params["capability_id"]),
                mode=body.mode, version=body.version,
            )
        except (LookupError, PermissionError, ValueError) as exc:
            return JSONResponse({"error": "catalog_selection_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse(result)

    async def _catalog_prepare(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, SelectCatalogCapabilityRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            plan = await self._capability_catalog.prepare(
                authenticated.device.principal_id,
                str(request.path_params["capability_id"]),
                mode=body.mode, version=body.version,
            )
        except (LookupError, OSError, PermissionError, ValueError) as exc:
            return JSONResponse({"error": "catalog_install_rejected", "detail": str(exc)[:1000]}, status_code=422)
        except Exception as exc:  # noqa: BLE001
            return self._core_error(exc)
        return JSONResponse({"plan": plan.model_dump(mode="json")}, status_code=201)

    async def _improvement_candidates(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        values = self._improvements.list_candidates(authenticated.device.principal_id)
        return JSONResponse({"candidates": [item.model_dump(mode="json") for item in values]})

    async def _improvement_evidence(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, RecordImprovementEvidenceRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        value = self._improvements.record_evidence(
            authenticated.device.principal_id, kind=body.kind,
            subject_ref=body.subject_ref, summary=body.summary,
        )
        return JSONResponse({"evidence": value.model_dump(mode="json")}, status_code=201)

    async def _improvement_case(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, CreateEvaluationCaseRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        value = self._improvements.add_case(
            authenticated.device.principal_id,
            sanitized_input=body.sanitized_input,
            expected_invariants=body.expected_invariants,
            fixture_results=body.fixture_results,
            dataset_version=body.dataset_version,
        )
        return JSONResponse({"case": value.model_dump(mode="json")}, status_code=201)

    async def _improvement_candidate(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, CreateImprovementCandidateRequest, max_body_bytes=600_000)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            value = self._improvements.create_candidate(
                authenticated.device.principal_id,
                **body.model_dump(mode="python"),
                author=authenticated.device.device_id,
            )
        except (LookupError, ValueError) as exc:
            return JSONResponse({"error": "candidate_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse({"candidate": value.model_dump(mode="json")}, status_code=201)

    async def _improvement_replay(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, ReplayImprovementCandidateRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            replay = self._improvements.replay(
                authenticated.device.principal_id,
                str(request.path_params["candidate_id"]),
                dataset_version=body.dataset_version,
            )
        except (LookupError, ValueError) as exc:
            return JSONResponse({"error": "replay_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse({"replay": replay.model_dump(mode="json")})

    async def _improvement_approve(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, ApproveImprovementCandidateRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            promotion = self._improvements.approve(
                authenticated.device.principal_id,
                str(request.path_params["candidate_id"]),
                approved_by=authenticated.device.device_id,
                canary_scope=body.canary_scope,
            )
        except (LookupError, ValueError) as exc:
            return JSONResponse({"error": "approval_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse({"promotion": promotion})

    async def _improvement_finish(self, request: Request) -> JSONResponse:
        parsed = await self._authorized_governance_body(request, FinishImprovementCanaryRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        authenticated, body = parsed
        try:
            candidate = self._improvements.finish_canary(
                authenticated.device.principal_id,
                str(request.path_params["candidate_id"]),
                promote=body.promote, metrics=body.metrics,
            )
        except (LookupError, ValueError) as exc:
            return JSONResponse({"error": "canary_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse({"candidate": candidate.model_dump(mode="json")})

    async def _improvement_rollback(self, request: Request) -> JSONResponse:
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            candidate = self._improvements.rollback(
                authenticated.device.principal_id,
                str(request.path_params["candidate_id"]),
            )
        except (LookupError, ValueError) as exc:
            return JSONResponse({"error": "rollback_rejected", "detail": str(exc)[:1000]}, status_code=422)
        return JSONResponse({"candidate": candidate.model_dump(mode="json")})

    async def _authorized_governance_body(self, request: Request, model, *, max_body_bytes: int = 16 * 1024):
        authenticated = self._authorize_configuration(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse_body(request, model, max_body_bytes=max_body_bytes)
        if isinstance(parsed, JSONResponse):
            return parsed
        return authenticated, parsed


__all__ = ["GovernanceRoutes"]
