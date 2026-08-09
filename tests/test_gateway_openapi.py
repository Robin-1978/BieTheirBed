from __future__ import annotations

import httpx
import pytest

from pc_assistant.config import AppConfig
from pc_assistant.gateway.adapter import SecureGatewayAdapter
from pc_assistant.gateway.openapi import gateway_openapi_schema


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

    assert schema["openapi"] == "3.1.0"
    assert set(schema["paths"]) == actual_paths - {"/openapi.json"}
    assert schema["components"]["securitySchemes"]["gatewaySession"]["scheme"] == (
        "bearer"
    )
    assert "grant_secret" in schema["components"]["schemas"]["PairCompleteRequest"][
        "properties"
    ]
    assert not any("method" in path for path in schema["paths"])
    assert schema["paths"]["/v1/tasks/{task_id}/retry"]["post"][
        "operationId"
    ] == "retryTask"
    assert schema["paths"]["/v1/runtime/status"]["get"]["operationId"] == (
        "getRuntimeStatus"
    )


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
