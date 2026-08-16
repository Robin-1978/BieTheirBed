from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from knoa_platform.hub.hosted import create_hosted_hub_app
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.relay_protocol import canonical_json

BOOTSTRAP_TOKEN = "bootstrap-" + "b" * 40
PASSWORD = "correct horse battery staple"


async def _grant(client: httpx.AsyncClient) -> dict:
    response = await client.post(
        "/v1/hosted/account-enrollment-grants",
        headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        json={"ttl_seconds": 600},
    )
    assert response.status_code == 201
    return response.json()


async def _create_account(
    client: httpx.AsyncClient,
    login_identity: str,
    display_name: str,
) -> dict:
    grant = await _grant(client)
    response = await client.post(
        "/v1/hosted/accounts",
        json={
            "grant_id": grant["grant_id"],
            "grant_secret": grant["secret"],
            "login_identity": login_identity,
            "display_name": display_name,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_hosted_single_node_creates_isolated_personal_workspaces(
    tmp_path: Path,
) -> None:
    app = create_hosted_hub_app(
        tmp_path / "hosted",
        hub_id="hub_hosted",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
        alpha = await _create_account(client, "alpha@example.com", "Alpha")
        beta = await _create_account(client, "beta@example.com", "Beta")

        assert alpha["account_id"] != beta["account_id"]
        assert alpha["workspace_id"] != beta["workspace_id"]
        assert alpha["hub_id"] == beta["hub_id"] == "hub_hosted"
        assert alpha["deployment_mode"] == "hosted_single_node"

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
        assert alpha_hub.json()["signing_public_key"] == beta_hub.json()[
            "signing_public_key"
        ]
        assert cross_tenant.status_code == 401

        account = await client.get("/v1/hosted/account", headers=alpha_headers)
        health = await client.get("/health")
        assert account.status_code == 200
        assert account.json()["workspaces"][0]["kind"] == "personal"
        assert health.json()["account_count"] == 2
        assert health.json()["workspace_count"] == 2


@pytest.mark.asyncio
async def test_account_grants_are_single_use_and_password_login_recovers_session(
    tmp_path: Path,
) -> None:
    app = create_hosted_hub_app(
        tmp_path / "hosted",
        hub_id="hub_hosted",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://hub",
    ) as client:
        grant = await _grant(client)
        body = {
            "grant_id": grant["grant_id"],
            "grant_secret": grant["secret"],
            "login_identity": "owner@example.com",
            "display_name": "Owner",
            "password": PASSWORD,
        }
        created = await client.post("/v1/hosted/accounts", json=body)
        replay = await client.post("/v1/hosted/accounts", json=body)
        rejected = await client.post(
            "/v1/hosted/sessions",
            json={"login_identity": "owner@example.com", "password": "wrong-password-1"},
        )
        logged_in = await client.post(
            "/v1/hosted/sessions",
            json={"login_identity": "OWNER@example.com", "password": PASSWORD},
        )

        assert created.status_code == 201
        assert replay.status_code == 401
        assert rejected.status_code == 401
        assert logged_in.status_code == 201
        assert logged_in.json()["workspace_id"] == created.json()["workspace_id"]
        assert logged_in.json()["access_token"] != created.json()["access_token"]

        headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
        changed = await client.patch(
            "/v1/hosted/account/password",
            headers=headers,
            json={
                "current_password": PASSWORD,
                "new_password": "a different secure password",
            },
        )
        revoked = await client.delete("/v1/hosted/session", headers=headers)
        after_revoke = await client.get("/v1/hosted/account", headers=headers)
        new_login = await client.post(
            "/v1/hosted/sessions",
            json={
                "login_identity": "owner@example.com",
                "password": "a different secure password",
            },
        )
        assert changed.status_code == 200
        assert revoked.status_code == 200
        assert after_revoke.status_code == 401
    assert new_login.status_code == 201


@pytest.mark.asyncio
async def test_password_reset_is_one_time_and_revokes_existing_sessions(
    tmp_path: Path,
) -> None:
    app = create_hosted_hub_app(
        tmp_path / "hosted",
        hub_id="hub_hosted",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://hub",
    ) as client:
        account = await _create_account(client, "owner@example.com", "Owner")
        reset_grant = await client.post(
            "/v1/hosted/password-reset-grants",
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
            json={"login_identity": "owner@example.com", "ttl_seconds": 600},
        )
        assert reset_grant.status_code == 201
        grant = reset_grant.json()
        reset_body = {
            "grant_id": grant["grant_id"],
            "grant_secret": grant["secret"],
            "new_password": "replacement secure password",
        }
        reset = await client.post("/v1/hosted/password-reset", json=reset_body)
        replay = await client.post("/v1/hosted/password-reset", json=reset_body)
        old_session = await client.get(
            "/v1/hosted/account",
            headers={"Authorization": f"Bearer {account['access_token']}"},
        )
        new_session = await client.get(
            "/v1/hosted/account",
            headers={"Authorization": f"Bearer {reset.json()['access_token']}"},
        )
        old_password = await client.post(
            "/v1/hosted/sessions",
            json={"login_identity": "owner@example.com", "password": PASSWORD},
        )

        assert reset.status_code == 201
        assert replay.status_code == 401
        assert old_session.status_code == 401
        assert new_session.status_code == 200
        assert old_password.status_code == 401


@pytest.mark.asyncio
async def test_hosted_workspace_creation_has_independent_storage_and_membership(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hosted"
    app = create_hosted_hub_app(
        root,
        hub_id="hub_hosted",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://hub",
    ) as client:
        alpha = await _create_account(client, "alpha@example.com", "Alpha")
        beta = await _create_account(client, "beta@example.com", "Beta")
        alpha_headers = {"Authorization": f"Bearer {alpha['access_token']}"}
        beta_headers = {"Authorization": f"Bearer {beta['access_token']}"}

        created = await client.post(
            "/v1/hosted/workspaces",
            headers=alpha_headers,
            json={"display_name": "Alpha Team", "kind": "shared"},
        )
        assert created.status_code == 201
        workspace = created.json()
        assert workspace["kind"] == "shared"
        assert (root / "tenants" / workspace["workspace_id"] / "hub.db").is_file()

        alpha_access = await client.get(
            f"{workspace['workspace_path']}/v1/hub",
            headers=alpha_headers,
        )
        beta_access = await client.get(
            f"{workspace['workspace_path']}/v1/hub",
            headers=beta_headers,
        )
        listed = await client.get("/v1/hosted/workspaces", headers=alpha_headers)
        assert alpha_access.status_code == 200
        assert beta_access.status_code == 401
        assert {item["workspace_id"] for item in listed.json()["workspaces"]} == {
            alpha["workspace_id"],
            workspace["workspace_id"],
        }

        added = await client.post(
            f"/v1/hosted/workspaces/{workspace['workspace_id']}/members",
            headers=alpha_headers,
            json={"login_identity": "beta@example.com", "role": "member"},
        )
        beta_workspaces = await client.get(
            "/v1/hosted/workspaces",
            headers=beta_headers,
        )
        beta_shared_access = await client.get(
            f"{workspace['workspace_path']}/v1/hub",
            headers=beta_headers,
        )
        beta_admin_rejected = await client.post(
            f"{workspace['workspace_path']}/v1/node-enrollment-grants",
            headers=beta_headers,
            json={"ttl_seconds": 600},
        )
        beta_members_rejected = await client.get(
            f"/v1/hosted/workspaces/{workspace['workspace_id']}/members",
            headers=beta_headers,
        )
        members = await client.get(
            f"/v1/hosted/workspaces/{workspace['workspace_id']}/members",
            headers=alpha_headers,
        )

        assert added.status_code == 201
        assert added.json()["role"] == "member"
        assert workspace["workspace_id"] in {
            item["workspace_id"] for item in beta_workspaces.json()["workspaces"]
        }
        assert beta_shared_access.status_code == 200
        assert beta_admin_rejected.status_code == 401
        assert beta_members_rejected.status_code == 403
        assert [item["role"] for item in members.json()["members"]] == [
            "owner",
            "member",
        ]

        removed = await client.delete(
            f"/v1/hosted/workspaces/{workspace['workspace_id']}/members/{beta['account_id']}",
            headers=alpha_headers,
        )
        after_remove = await client.get(
            f"{workspace['workspace_path']}/v1/hub",
            headers=beta_headers,
        )
        assert removed.status_code == 200
        assert after_remove.status_code == 401


@pytest.mark.asyncio
async def test_hosted_workspace_enrollment_and_directory_are_tenant_scoped(
    tmp_path: Path,
) -> None:
    app = create_hosted_hub_app(
        tmp_path / "hosted",
        hub_id="hub_hosted",
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
            "hub_id": "hub_hosted",
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
        assert enrolled.json()["hub"]["hub_id"] == "hub_hosted"

        alpha_nodes = await client.get(f"{alpha_url}/v1/nodes", headers=alpha_headers)
        beta_nodes = await client.get(f"{beta_url}/v1/nodes", headers=beta_headers)
        assert [item["node_id"] for item in alpha_nodes.json()["nodes"]] == [
            node.node_id
        ]
        assert beta_nodes.json()["nodes"] == []


@pytest.mark.asyncio
async def test_hosted_control_and_tenant_state_survive_application_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hosted"
    first = create_hosted_hub_app(
        root,
        hub_id="hub_hosted",
        bootstrap_token=BOOTSTRAP_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first),
        base_url="http://hub",
    ) as client:
        account = await _create_account(client, "owner@example.com", "Owner")

    second = create_hosted_hub_app(
        root,
        hub_id="hub_hosted",
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
        logged_in = await client.post(
            "/v1/hosted/sessions",
            json={"login_identity": "owner@example.com", "password": PASSWORD},
        )

    assert response.status_code == 200
    assert response.json()["workspace_id"] == account["workspace_id"]
    assert logged_in.status_code == 201
