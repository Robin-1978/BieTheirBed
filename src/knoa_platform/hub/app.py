"""Deployable single-Hub HTTP control plane and opaque WebSocket Relay."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from knoa_platform.hub.relay import RelayBroker, RelayFrame
from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InstallationRequest(_Request):
    installation_id: str = Field(min_length=1, max_length=128)
    public_key: str = Field(min_length=40, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)


class EnrollmentGrantRequest(_Request):
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


class NodeEnrollmentRequest(_Request):
    audience: Literal["knoa-node-enrollment-v1"]
    hub_id: str = Field(min_length=1, max_length=128)
    grant_id: str = Field(min_length=1, max_length=128)
    grant_secret: str = Field(min_length=32, max_length=256)
    challenge: str = Field(min_length=16, max_length=256)
    node_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    signing_public_key: str = Field(min_length=40, max_length=64)
    signing_key_version: int = Field(ge=1)
    configuration_public_key: str = Field(min_length=40, max_length=64)
    configuration_key_version: int = Field(ge=1)
    platform: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=64)
    signature: str = Field(min_length=80, max_length=128)


class PresenceRequest(_Request):
    node_id: str = Field(min_length=1, max_length=128)
    timestamp: float
    nonce: str = Field(min_length=16, max_length=256)
    direct_gateway_url: str = Field(default="", max_length=2048)
    signature: str = Field(min_length=80, max_length=128)


class TicketRequest(_Request):
    installation_id: str = Field(min_length=1, max_length=128)
    node_id: str = Field(min_length=1, max_length=128)
    transport: str = Field(pattern=r"^(direct|relay)$")


class FleetEnvelopeRequest(_Request):
    node_id: str = Field(min_length=1, max_length=128)
    expected_base_revision_digest: str = Field(min_length=64, max_length=64)
    candidate_digest: str = Field(min_length=64, max_length=64)
    sealed_candidate: str = Field(min_length=1, max_length=2_000_000)
    expires_at: float


class FleetRolloutRequest(_Request):
    rollout_id: str = Field(min_length=1, max_length=128)
    installation_id: str = Field(min_length=1, max_length=128)
    envelopes: tuple[FleetEnvelopeRequest, ...] = Field(min_length=1, max_length=100)


class FleetReportRequest(PresenceRequest):
    state: str = Field(pattern=r"^(applied|failed|skipped)$")
    result_code: str = Field(default="", max_length=128)


class WorkspaceResourceRequest(_Request):
    resource_id: str = Field(min_length=1, max_length=128)
    kind: Literal["model", "mcp"]
    generation: int = Field(ge=1)
    canonical_digest: str = Field(min_length=64, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    spec: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class DeploymentRequest(_Request):
    deployment_id: str = Field(min_length=1, max_length=128)
    kind: Literal["model", "mcp"]
    resource_id: str = Field(min_length=1, max_length=128)
    resource_generation: int = Field(ge=1)
    resource_digest: str = Field(min_length=64, max_length=64)
    target_node_id: str = Field(min_length=1, max_length=128)
    desired_generation: int = Field(ge=1)
    spec: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class WorkProjectionRequest(_Request):
    node_id: str = Field(min_length=1, max_length=128)
    entity_kind: Literal["conversation", "task"]
    entity_id: str = Field(min_length=1, max_length=256)
    principal_id: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=200)
    state: str = Field(min_length=1, max_length=64)
    progress: float | None = Field(default=None, ge=0, le=1)
    summary: str = Field(default="", max_length=4000)
    approval_summary: str = Field(default="", max_length=2000)
    artifact_refs: tuple[dict[str, Any], ...] = Field(default=(), max_length=100)
    source_generation: int = Field(default=1, ge=1)
    source_digest: str = Field(min_length=64, max_length=64)
    projection_seq: int = Field(ge=1)
    source_created_at: float = Field(ge=0)
    source_updated_at: float = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: float = Field(ge=0)
    signature: str = Field(min_length=80, max_length=128)


class ResourceGrantRequest(_Request):
    grant_id: str = Field(min_length=1, max_length=128)
    caller_node_id: str = Field(min_length=1, max_length=128)
    target_deployment_id: str = Field(min_length=1, max_length=128)
    capability: Literal["model_inference", "mcp_invoke"] = "model_inference"
    max_request_deadline: float = Field(gt=0, le=3600)
    expires_at: float


class DeploymentObservationRequest(_Request):
    node_id: str = Field(min_length=1, max_length=128)
    deployment_id: str = Field(min_length=1, max_length=128)
    applied_digest: str = Field(min_length=64, max_length=64)
    health_epoch: int = Field(ge=1)
    health: str = Field(pattern=r"^(healthy|degraded|unavailable)$")
    capabilities: dict = Field(default_factory=dict)
    available_capacity: int = Field(ge=0, le=64)
    observed_at: float
    expires_at: float
    signature: str = Field(min_length=80, max_length=128)


class ResourceTicketRequest(_Request):
    invocation_id: str = Field(min_length=1, max_length=128)
    caller_node_id: str = Field(min_length=1, max_length=128)
    target_deployment_id: str = Field(min_length=1, max_length=128)
    max_deadline: float = Field(gt=0, le=3600)
    timestamp: float
    nonce: str = Field(min_length=16, max_length=256)
    signature: str = Field(min_length=80, max_length=128)


class InvocationObservationRequest(_Request):
    node_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=128)
    reported_state: str = Field(
        pattern=r"^(admitted|running|completed|failed|cancel_requested|cancelled|outcome_unknown)$"
    )
    execution_epoch: str = Field(default="", max_length=128)
    report_seq: int = Field(ge=0)
    usage_summary: dict = Field(default_factory=dict)
    observed_at: float
    signature: str = Field(min_length=80, max_length=128)


class HubApplication:
    def __init__(
        self,
        service: HubService,
        *,
        deployment_mode: str = "self_hosted",
    ) -> None:
        self.service = service
        self.deployment_mode = deployment_mode
        self.relay = RelayBroker()
        self.app = Starlette(
            routes=[
                Route("/health", self.health, methods=["GET"]),
                Route("/v1/hub", self.hub, methods=["GET"]),
                Route("/v1/installations", self.installations, methods=["POST"]),
                Route("/v1/nodes", self.nodes, methods=["GET"]),
                Route("/v1/node-enrollment-grants", self.enrollment_grants, methods=["POST"]),
                Route("/v1/nodes/enroll", self.enroll, methods=["POST"]),
                Route("/v1/nodes/presence", self.presence, methods=["POST"]),
                Route("/v1/workspace", self.workspace, methods=["GET"]),
                Route("/v1/workspace-resources", self.workspace_resources, methods=["GET", "POST"]),
                Route("/v1/deployments", self.deployments, methods=["GET", "POST"]),
                Route("/v1/work-projections", self.work_projections, methods=["GET", "POST"]),
                Route("/v1/resource-grants", self.resource_grants, methods=["GET", "POST"]),
                Route(
                    "/v1/resource-grants/{grant_id:str}",
                    self.resource_grant,
                    methods=["DELETE"],
                ),
                Route("/v1/deployment-observations", self.deployment_observations, methods=["GET", "POST"]),
                Route("/v1/resource-invocation-tickets", self.resource_invocation_tickets, methods=["POST"]),
                Route(
                    "/v1/resource-invocations/{invocation_id:str}/observations",
                    self.invocation_observations,
                    methods=["GET", "POST"],
                ),
                Route("/v1/connection-tickets", self.tickets, methods=["POST"]),
                Route("/v1/fleet/rollouts", self.rollouts, methods=["POST"]),
                Route("/v1/nodes/{node_id:str}/fleet/pull", self.fleet_pull, methods=["POST"]),
                Route("/v1/fleet/rollouts/{rollout_id:str}/nodes/{node_id:str}/report", self.fleet_report, methods=["POST"]),
                WebSocketRoute("/v1/relay/node", self.relay_node),
                WebSocketRoute("/v1/relay/client", self.relay_client),
                WebSocketRoute("/v1/relay/resource-client", self.relay_resource_client),
            ]
        )

    async def health(self, _request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "hub_id": self.service.hub_id})

    async def hub(self, request: Request) -> JSONResponse:
        authenticated = self._member(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        return JSONResponse(
            {
                "hub_id": self.service.hub_id,
                "workspace_id": self.service.workspace_id,
                "identity_issuer_id": self.service.hub_id,
                "deployment_mode": self.deployment_mode,
                "signing_public_key": self.service.signing_public_key,
            }
        )

    async def installations(self, request: Request) -> JSONResponse:
        authenticated = self._member(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse(request, InstallationRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            record = self.service.repository.register_installation(
                authenticated,
                parsed.installation_id,
                parsed.public_key,
                parsed.display_name,
            )
        except PermissionError:
            return JSONResponse({"error": "rejected"}, status_code=403)
        return JSONResponse(record, status_code=201)

    async def nodes(self, request: Request) -> JSONResponse:
        authenticated = self._member(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        now = time.time()
        relay_nodes = await self.relay.connected_node_ids()
        nodes = []
        for item in self.service.repository.list_nodes():
            nodes.append(
                {
                    **item,
                    "online": bool(
                        item["node_id"] in relay_nodes
                        or (item["last_seen"] and now - item["last_seen"] < 90)
                    ),
                }
            )
        return JSONResponse({"nodes": nodes})

    async def enrollment_grants(self, request: Request) -> JSONResponse:
        if (error := self._owner(request)) is not None:
            return error
        parsed = await self._parse(request, EnrollmentGrantRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        grant = self.service.repository.create_enrollment_grant(parsed.ttl_seconds)
        return JSONResponse(grant.__dict__, status_code=201)

    async def enroll(self, request: Request) -> JSONResponse:
        parsed = await self._parse(request, NodeEnrollmentRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            node = self.service.enroll_node(parsed.model_dump(mode="json"))
        except PermissionError:
            return JSONResponse({"error": "rejected"}, status_code=401)
        except (KeyError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse(
            {
                "node": node,
                "hub": {
                    "hub_id": self.service.hub_id,
                    "signing_public_key": self.service.signing_public_key,
                },
            },
            status_code=201,
        )

    async def presence(self, request: Request) -> JSONResponse:
        parsed = await self._parse(request, PresenceRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            node = self.service.record_presence(parsed.model_dump(mode="json"))
        except (PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse({"node_id": node["node_id"], "observed_at": node["last_seen"]})

    async def workspace(self, request: Request) -> JSONResponse:
        authenticated = self._member(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        return JSONResponse({"workspace": self.service.repository.workspace()})

    async def workspace_resources(self, request: Request) -> JSONResponse:
        authenticated = self._authenticate(request, admin=request.method != "GET")
        if isinstance(authenticated, JSONResponse):
            return authenticated
        if request.method == "GET":
            kind = request.query_params.get("kind", "")
            try:
                resources = self.service.repository.list_workspace_resources(kind=kind)
            except ValueError:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            return JSONResponse({"resources": list(resources)})
        parsed = await self._parse(request, WorkspaceResourceRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            item = self.service.repository.put_workspace_resource(
                parsed.model_dump(mode="json"), created_by=authenticated
            )
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse({"resource": item}, status_code=201)

    async def deployments(self, request: Request) -> JSONResponse:
        authenticated = self._authenticate(request, admin=request.method != "GET")
        if isinstance(authenticated, JSONResponse):
            return authenticated
        if request.method == "GET":
            try:
                deployments = self.service.repository.list_deployments(
                    kind=request.query_params.get("kind", ""),
                    node_id=request.query_params.get("node_id", ""),
                )
            except ValueError:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            return JSONResponse({"deployments": list(deployments)})
        parsed = await self._parse(request, DeploymentRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            item = self.service.repository.put_deployment(parsed.model_dump(mode="json"))
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse({"deployment": item}, status_code=201)

    async def work_projections(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            authenticated = self._member(request)
            if isinstance(authenticated, JSONResponse):
                return authenticated
            try:
                items = self.service.repository.list_work_projections(
                    entity_kind=request.query_params.get("kind", ""),
                    node_id=request.query_params.get("node_id", ""),
                    limit=int(request.query_params.get("limit", "200")),
                )
            except (TypeError, ValueError):
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            return JSONResponse({"items": list(items)})
        parsed = await self._parse(request, WorkProjectionRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            item = self.service.publish_work_projection(parsed.model_dump(mode="json"))
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse({"item": item}, status_code=201)

    async def resource_grants(self, request: Request) -> JSONResponse:
        authenticated = self._authenticate(
            request,
            admin=request.method != "GET",
        )
        if isinstance(authenticated, JSONResponse):
            return authenticated
        if request.method == "GET":
            return JSONResponse(
                {"grants": list(self.service.repository.list_resource_grants())}
            )
        parsed = await self._parse(request, ResourceGrantRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            if parsed.expires_at <= time.time():
                raise ValueError("Resource grant is already expired")
            item = self.service.repository.put_resource_grant(
                parsed.model_dump(mode="json")
            )
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse({"grant": item}, status_code=201)

    async def resource_grant(self, request: Request) -> JSONResponse:
        authenticated = self._authenticate(request, admin=True)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        grant_id = str(request.path_params.get("grant_id", "")).strip()
        if not grant_id:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            item = self.service.repository.revoke_resource_grant(grant_id)
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({"grant": item})

    async def deployment_observations(self, request: Request) -> JSONResponse:
        if request.method == "GET":
            authenticated = self._member(request)
            if isinstance(authenticated, JSONResponse):
                return authenticated
            return JSONResponse(
                {
                    "observations": list(
                        self.service.repository.list_deployment_observations()
                    )
                }
            )
        parsed = await self._parse(request, DeploymentObservationRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            item = self.service.publish_deployment_observation(
                parsed.model_dump(mode="json")
            )
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse({"observation": item}, status_code=201)

    async def resource_invocation_tickets(self, request: Request) -> JSONResponse:
        parsed = await self._parse(request, ResourceTicketRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            ticket = self.service.issue_resource_ticket(
                parsed.model_dump(mode="json")
            )
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=403)
        return JSONResponse({"ticket": ticket})

    async def invocation_observations(self, request: Request) -> JSONResponse:
        invocation_id = str(request.path_params["invocation_id"])
        if request.method == "GET":
            authenticated = self._member(request)
            if isinstance(authenticated, JSONResponse):
                return authenticated
            return JSONResponse(
                {
                    "observations": list(
                        self.service.repository.list_invocation_observations(
                            invocation_id
                        )
                    )
                }
            )
        parsed = await self._parse(request, InvocationObservationRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        if parsed.invocation_id != invocation_id:
            return JSONResponse({"error": "rejected"}, status_code=401)
        try:
            self.service.record_invocation_observation(
                parsed.model_dump(mode="json")
            )
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse({"accepted": True})

    async def tickets(self, request: Request) -> JSONResponse:
        authenticated = self._member(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse(request, TicketRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            token = self.service.issue_ticket(
                parsed.installation_id,
                parsed.node_id,
                parsed.transport,
                subject_id=authenticated,
            )
        except (LookupError, PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse({"ticket": token})

    async def rollouts(self, request: Request) -> JSONResponse:
        if (error := self._owner(request)) is not None:
            return error
        parsed = await self._parse(request, FleetRolloutRequest, max_bytes=4_000_000)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            self.service.repository.installation(parsed.installation_id)
            self.service.repository.put_rollout(
                parsed.rollout_id,
                parsed.installation_id,
                tuple(item.model_dump(mode="json") for item in parsed.envelopes),
            )
        except (LookupError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse({"rollout_id": parsed.rollout_id, "state": "pending"}, status_code=201)

    async def fleet_pull(self, request: Request) -> JSONResponse:
        parsed = await self._parse(request, PresenceRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        if parsed.node_id != request.path_params["node_id"]:
            return JSONResponse({"error": "rejected"}, status_code=401)
        try:
            self.service.record_presence(parsed.model_dump(mode="json"))
            envelopes = self.service.repository.pending_envelopes(parsed.node_id)
        except (PermissionError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=401)
        return JSONResponse({"envelopes": list(envelopes)})

    async def fleet_report(self, request: Request) -> JSONResponse:
        parsed = await self._parse(request, FleetReportRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        node_id = str(request.path_params["node_id"])
        if parsed.node_id != node_id:
            return JSONResponse({"error": "rejected"}, status_code=401)
        try:
            self.service.record_presence(parsed.model_dump(mode="json", include={"node_id", "timestamp", "nonce", "signature"}))
            self.service.repository.report_envelope(
                str(request.path_params["rollout_id"]), node_id, parsed.state, parsed.result_code
            )
        except (PermissionError, LookupError, ValueError):
            return JSONResponse({"error": "rejected"}, status_code=422)
        return JSONResponse({"accepted": True})

    async def relay_node(self, websocket: WebSocket) -> None:
        await websocket.accept()
        connection = None
        node_id = ""
        try:
            authentication = PresenceRequest.model_validate(await websocket.receive_json())
            self.service.record_presence(authentication.model_dump(mode="json"))
            node_id = authentication.node_id
            connection = await self.relay.register_node(node_id, websocket)
            await websocket.send_json({"ready": True, "node_id": node_id})
            while True:
                raw = await websocket.receive_json()
                frame = RelayFrame.model_validate(raw.get("frame"))
                frame.validate_bounds()
                await self.relay.send_to_client(node_id, frame)
        except (WebSocketDisconnect, ValidationError, PermissionError, ValueError):
            pass
        finally:
            if connection is not None:
                await self.relay.unregister_node(node_id, connection)

    async def relay_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = ""
        node_id = ""
        try:
            first = await websocket.receive_json()
            ticket = self.service.verify_and_consume_ticket(str(first.get("ticket", "")))
            if ticket["transport"] != "relay":
                raise PermissionError("Relay ticket required")
            session_id = str(ticket["ticket_id"])
            node_id = str(ticket["node_id"])
            await self.relay.register_client(
                session_id,
                node_id,
                websocket,
            )
            await websocket.send_json({"ready": True, "session_id": session_id})
            while True:
                raw = await websocket.receive_json()
                frame = RelayFrame.model_validate(raw.get("frame"))
                frame.validate_bounds()
                if frame.session_id != session_id:
                    raise ValueError("Relay session ID mismatch")
                await self.relay.send_to_node(node_id, frame)
        except LookupError:
            await websocket.close(code=4404, reason="node offline")
        except (WebSocketDisconnect, ValidationError, PermissionError, ValueError):
            pass
        finally:
            if session_id:
                await self.relay.unregister_client(session_id)
                if node_id:
                    try:
                        await self.relay.send_to_node(
                            node_id,
                            RelayFrame(
                                session_id=session_id,
                                stream_id=0,
                                frame_type="reset",
                                sequence=0,
                                ciphertext_length=0,
                            ),
                        )
                    except Exception:  # noqa: BLE001, S110
                        pass

    async def relay_resource_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = ""
        node_id = ""
        try:
            first = await websocket.receive_json()
            ticket = self.service.verify_resource_ticket(str(first.get("ticket", "")))
            session_id = str(ticket["ticket_id"])
            node_id = str(ticket["target_node_id"])
            await self.relay.register_client(session_id, node_id, websocket)
            await websocket.send_json({"ready": True, "session_id": session_id})
            while True:
                raw = await websocket.receive_json()
                frame = RelayFrame.model_validate(raw.get("frame"))
                frame.validate_bounds()
                if frame.session_id != session_id:
                    raise ValueError("Relay session ID mismatch")
                await self.relay.send_to_node(node_id, frame)
        except LookupError:
            await websocket.close(code=4404, reason="node offline")
        except (WebSocketDisconnect, ValidationError, PermissionError, ValueError):
            pass
        finally:
            if session_id:
                await self.relay.unregister_client(session_id)
                if node_id:
                    try:
                        await self.relay.send_to_node(
                            node_id,
                            RelayFrame(
                                session_id=session_id,
                                stream_id=0,
                                frame_type="reset",
                                sequence=0,
                                ciphertext_length=0,
                            ),
                        )
                    except Exception:  # noqa: BLE001, S110
                        pass

    def _owner(self, request: Request) -> JSONResponse | None:
        authenticated = self._authenticate(request, admin=True)
        return authenticated if isinstance(authenticated, JSONResponse) else None

    def _member(self, request: Request) -> str | JSONResponse:
        return self._authenticate(request, admin=False)

    def _authenticate(
        self,
        request: Request,
        *,
        admin: bool,
    ) -> str | JSONResponse:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise PermissionError
            if admin:
                return self.service.authenticate_owner(token)
            return self.service.authenticate_member(token)
        except PermissionError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    @staticmethod
    async def _parse(request: Request, model, *, max_bytes: int = 1024 * 1024):
        body = await request.body()
        if len(body) > max_bytes:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            return model.model_validate_json(body)
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)


def create_hub_app(
    root: str | Path,
    *,
    hub_id: str,
    owner_token: str,
) -> Starlette:
    resolved = Path(root).expanduser().resolve()
    repository = HubRepository(resolved / "hub.db", hub_id=hub_id)
    service = HubService(
        repository,
        resolved / "hub-signing.key",
        owner_token=owner_token,
    )
    return HubApplication(service).app


__all__ = ["HubApplication", "create_hub_app"]
