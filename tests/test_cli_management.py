from __future__ import annotations

from types import SimpleNamespace

import pytest

from knoa_platform.cli_management import _mcp_event_policy, run_client_command
from knoa_platform.service.core_api import CoreError, MCPPackageDeploymentSnapshot
from knoa_platform.service.core_client import CoreRequestError


def test_mcp_event_policy_supports_descendant_events_without_collection_root() -> None:
    policy = _mcp_event_policy(
        {
            "server_id": "jira",
            "resource_uri": "jira://assigned-to-me/events",
            "descendants_only": True,
        }
    )

    assert policy.source_config == {
        "resource_uri_prefix": "jira://assigned-to-me/events",
        "include_root": False,
        "include_descendants": True,
    }


@pytest.mark.asyncio
async def test_explicit_cli_mcp_deployment_calls_owner_admin_api(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    source = tmp_path / "jira-provider"
    source.mkdir()

    class Client:
        def __init__(self) -> None:
            self.call = None
            self.disconnected = False

        async def deploy_mcp_package(self, path, server_id, **kwargs):
            self.call = (path, server_id, kwargs)
            return MCPPackageDeploymentSnapshot(
                action="updated",
                server_id=server_id,
                extension_id=f"mcp:{server_id}",
                state="running",
                tools=(f"mcp__{server_id}__ping",),
                resource_task="",
            )

        async def disconnect(self):
            self.disconnected = True

    client = Client()

    async def connect(_config):
        return client

    monkeypatch.setattr(
        "knoa_platform.cli_management.get_core_client",
        connect,
    )

    result = await run_client_command(
        SimpleNamespace(),
        "mcp-package-deploy",
        path=str(source),
        server_id="jira",
    )

    assert result == 0
    assert client.call == (
        str(source.resolve()),
        "jira",
        {},
    )
    assert client.disconnected
    assert '"state": "running"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_management_cli_reports_core_error_without_traceback(
    monkeypatch,
    capsys,
) -> None:
    class Client:
        async def deploy_mcp_package(self, *_args, **_kwargs):
            raise CoreRequestError(
                CoreError(
                    request_id="request-a",
                    code="invalid_request",
                    message="MCP import source must be a directory",
                    correlation_id="correlation-a",
                )
            )

        async def disconnect(self):
            return None

    async def connect(_config):
        return Client()

    monkeypatch.setattr(
        "knoa_platform.cli_management.get_core_client",
        connect,
    )

    result = await run_client_command(
        SimpleNamespace(),
        "mcp-package-deploy",
        path="/tmp/missing-provider",
        server_id="smokecheck",
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "Error: MCP import source must be a directory\n"
    )
