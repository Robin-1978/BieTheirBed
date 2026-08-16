from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from knoa_platform.hub.hosted import create_hosted_hub_app
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.relay_protocol import canonical_json

BOOTSTRAP_TOKEN = "bootstrap-" + "b" * 40


async def _create_account(
    client: httpx.AsyncClient,
    login_identity: str,
    display_name: str,
) -> dict:
    response = await client.post(
        "/v1/hosted/accounts",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        json={
            "login_identity": login_identity,
            "display_name": display_name,
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_hosted_simulation_creates_isolated_personal_workspaces(
    tmp_path: Path,
) -> None:
    app = create_hosted_hub_app(
        tmp_path / "hosted",
        hub_id="hub_hosted_sim",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
        alpha = await _create_account(client, "alpha@example.com", "Alpha")
        beta = await _create_account(client, "beta@example.com", "Beta")

        assert alpha["workspace_id"] != beta["workspace_id"]
        assert alpha["hub_id"] == beta["hub_id"] == "hub_hosted_sim"
        assert alpha["identity_issuer_id"] == "hub_hosted_sim"

        alpha_url = alpha["workspace_path"]
        beta_url = beta["workspace_path"]
        alpha_headers = {"Authorization": f"Bearer {alpha['access_token']}"}
        beta_headers = {"Authorization": f"Bearer {beta['access_token']}"}

        alpha_hub = await client.get(f"{alpha_url}/v1/hub", headers=alpha_headers)
        beta_hub = await client.get(f"{beta_url}/v1/hub", headers=beta_headers)
        cross_tenant = await client.get(f"{beta_url}/v1/hub", headers=alpha_headers)

        assert alpha_hub.status_code == beta_hub.status_code == 200
        assert alpha_hub.json()["workspace_id"] == alpha["workspace_id"]
        assert beta_hub.json()["workspace_id"] == beta["workspace_id"]
        assert alpha_hub.json()["deployment_mode"] == "hosted_simulation"
        assert alpha_hub.json()["signing_public_key"] == beta_hub.json()[
            "signing_public_key"
        ]
        assert cross_tenant.status_code == 401

        status = await client.get(
            "/v1/hosted/account",
            headers=alpha_headers,
        )
        health = await client.get("/health")
        assert status.status_code == 200
        assert status.json()["workspace_id"] == alpha["workspace_id"]
        assert health.json()["tenant_count"] == 2


@pytest.mark.asyncio
async def test_hosted_workspace_enrollment_and_directory_are_tenant_scoped(
    tmp_path: Path,
) -> None:
    app = create_hosted_hub_app(
        tmp_path / "hosted",
        hub_id="hub_hosted_sim",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
        alpha = await _create_account(client, "alpha@example.com", "Alpha")
        beta = await _create_account(client, "beta@example.com", "Beta")
        alpha_headers = {"Authorization": f"Bearer {alpha['access_token']}"}
        beta_headers = {"Authorization": f"Bearer {beta['access_token']}"}
        alpha_url = alpha["workspace_path"]
        beta_url = beta["workspace_path"]

        grant_response = await client.post(
            f"{alpha_url}/v1/node-enrollment-grants",
            headers=alpha_headers,
            json={"ttl_seconds": 600},
        )
        assert grant_response.status_code == 201
        grant = grant_response.json()
        node = NodeIdentityStore(tmp_path / "node.json").load_or_create()
        transcript = {
            "audience": "knoa-node-enrollment-v1",
            "hub_id": "hub_hosted_sim",
            "grant_id": grant["grant_id"],
            "challenge": grant["challenge"],
            "node_id": node.node_id,
            "signing_public_key": node.signing_public_key,
            "signing_key_version": node.signing_key_version,
            "configuration_public_key": node.configuration_public_key,
            "configuration_key_version": node.configuration_key_version,
        }
        enrolled = await client.post(
            f"{alpha_url}/v1/nodes/enroll",
            json={
                **transcript,
                "grant_secret": grant["secret"],
                "display_name": "Alpha Node",
                "platform": "linux",
                "version": "1",
                "signature": node.sign(canonical_json(transcript)),
            },
        )
        assert enrolled.status_code == 201
        assert enrolled.json()["hub"]["hub_id"] == "hub_hosted_sim"

        alpha_nodes = await client.get(f"{alpha_url}/v1/nodes", headers=alpha_headers)
        beta_nodes = await client.get(f"{beta_url}/v1/nodes", headers=beta_headers)
        assert [item["node_id"] for item in alpha_nodes.json()["nodes"]] == [
            node.node_id
        ]
        assert beta_nodes.json()["nodes"] == []


@pytest.mark.asyncio
async def test_hosted_account_token_survives_application_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hosted"
    first = create_hosted_hub_app(
        root,
        hub_id="hub_hosted_sim",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first),
        base_url="http://hub",
    ) as client:
        account = await _create_account(client, "owner@example.com", "Owner")

    second = create_hosted_hub_app(
        root,
        hub_id="hub_hosted_sim",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second),
        base_url="http://hub",
    ) as client:
        response = await client.get(
            f"{account['workspace_path']}/v1/hub",
            headers={"Authorization": f"Bearer {account['access_token']}"},
        )
        duplicate = await client.post(
            "/v1/hosted/accounts",
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
            json={"login_identity": "owner@example.com", "display_name": "Other"},
        )

    assert response.status_code == 200
    assert response.json()["workspace_id"] == account["workspace_id"]
    assert duplicate.status_code == 409
