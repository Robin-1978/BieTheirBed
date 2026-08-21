from __future__ import annotations

import httpx
import pytest

from knoa_platform.config import AppConfig
from knoa_platform.gateway.adapter import SecureGatewayAdapter
from knoa_platform.gateway.openapi import gateway_openapi_schema


def _adapter(tmp_path) -> SecureGatewayAdapter:
    return SecureGatewayAdapter(
        AppConfig(
            fallback_enabled=False,
            runtime_root=str(tmp_path),
            gateway_enabled=True,
            gateway_port=0,
        )
    )


def test_gateway_openapi_matches_the_allow_listed_http_surface(tmp_path) -> None:
    adapter = _adapter(tmp_path)
    schema = gateway_openapi_schema()
    actual_paths = {route.path.replace(":str}", "}") for route in adapter.app.routes}
    local_console_paths = {
        "/console",
        "/v1/console/status",
        "/v1/console/diagnostics",
        "/v1/console/hub/enroll",
        "/v1/console/pairing",
        "/v1/console/lifecycle",
        "/v1/console/lifecycle/actions",
        "/v1/console/lifecycle/bundles/{name}",
        "/v1/console/config",
        "/v1/console/config/publish",
        "/v1/console/workspace-resources",
        "/v1/console/secrets/{reference}",
    }

    assert schema["openapi"] == "3.1.0"
    assert set(schema["paths"]) == actual_paths - {"/openapi.json"} - local_console_paths
    assert schema["components"]["securitySchemes"]["gatewaySession"]["scheme"] == (
        "bearer"
    )
    assert "grant_secret" in schema["components"]["schemas"]["PairCompleteRequest"][
        "properties"
    ]
    assert not any("method" in path for path in schema["paths"])
    assert schema["paths"]["/v1/task-executions/{execution_id}/rerun"]["post"][
        "operationId"
    ] == "rerunTaskExecution"
    assert schema["paths"]["/v1/runtime/status"]["get"]["operationId"] == (
        "getRuntimeStatus"
    )
    assert schema["paths"]["/v1/device/audit"]["get"]["operationId"] == (
        "listDeviceAudit"
    )
    assert schema["paths"]["/v1/task-executions/{execution_id}/events"]["get"][
        "operationId"
    ] == "listTaskExecutionEvents"
    assert schema["paths"]["/v1/device"]["delete"]["operationId"] == (
        "revokeCurrentDevice"
    )
    assert schema["paths"]["/v1/conversations/sessions"]["get"]["operationId"] == (
        "listConversationSessions"
    )
    assert schema["paths"]["/v1/conversations/turns/{turn_id}/retry"]["post"]["operationId"] == (
        "retryChatTurn"
    )
    assert schema["paths"]["/v1/agents"]["get"]["operationId"] == "listAgents"


@pytest.mark.asyncio
async def test_gateway_serves_codegen_contract_without_authentication(tmp_path) -> None:
    adapter = _adapter(tmp_path)
    transport = httpx.ASGITransport(app=adapter.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway.local") as http:
        response = await http.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Knoa Secure Gateway"
    assert response.json()["paths"]["/v1/events"]["get"]["operationId"] == (
        "streamTaskEvents"
    )
