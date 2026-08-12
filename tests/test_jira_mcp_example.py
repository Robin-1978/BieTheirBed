from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from examples.jira_mcp_server.jira_client import (
    JiraClient,
    JiraSettings,
    JiraStateStore,
)
from pc_assistant.extensions.mcp import StdioMCPClient
from pc_assistant.extensions.models import MCPServerConfig


def _settings(tmp_path: Path, *, write_enabled: bool = False) -> JiraSettings:
    return JiraSettings(
        base_url="https://jira.example.test",
        username="owner@example.test",
        api_token="secret",
        auth_mode="basic",
        api_version="2",
        jql="assignee = currentUser() AND statusCategory != Done",
        poll_interval_seconds=60,
        retention_days=7,
        max_issues=100,
        state_path=tmp_path / "jira.db",
        write_enabled=write_enabled,
    )


@pytest.mark.asyncio
async def test_assignment_transition_becomes_one_immutable_resource_event(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = JiraStateStore(settings.state_path)
    client = JiraClient(settings, store)

    async def current_user_ids():
        return {"owner", "owner@example.test"}

    async def search_assigned_issues():
        return (
            {
                "id": "10001",
                "key": "PROJECT-123",
                "fields": {"created": "2026-08-12T00:00:00.000+0000"},
                "changelog": {
                    "histories": [
                        {
                            "id": "9001",
                            "items": [
                                {
                                    "field": "assignee",
                                    "to": "owner",
                                    "toString": "Owner",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    client.current_user_ids = current_user_ids  # type: ignore[method-assign]
    client.search_assigned_issues = search_assigned_issues  # type: ignore[method-assign]
    try:
        first = await client.poll_assignment_events()
        repeated = await client.poll_assignment_events()
    finally:
        await client.close()

    assert len(first) == 1
    assert first[0]["issue_key"] == "PROJECT-123"
    assert repeated == ()
    assert store.list_assignment_events()[0]["event_id"] == first[0]["event_id"]


@pytest.mark.asyncio
async def test_comment_idempotency_replays_success_without_second_write(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, write_enabled=True)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    posts = 0

    async def find_marker(_issue_key: str, _marker: str) -> str:
        return ""

    async def request(method: str, path: str, **kwargs):
        nonlocal posts
        assert method == "POST"
        assert path.endswith("/issue/PROJECT-123/comment")
        assert "knoa-operation" in str(kwargs["json"])
        posts += 1
        return {"id": "comment-1"}

    client._find_comment_marker = find_marker  # type: ignore[method-assign]
    client._request = request  # type: ignore[method-assign]
    try:
        first = await client.add_comment("PROJECT-123", "Analysis result", "op-1")
        repeated = await client.add_comment("PROJECT-123", "Analysis result", "op-1")
    finally:
        await client.close()

    assert first == {
        "status": "succeeded",
        "comment_id": "comment-1",
        "replayed": False,
    }
    assert repeated == {
        "status": "succeeded",
        "comment_id": "comment-1",
        "replayed": True,
    }
    assert posts == 1


@pytest.mark.asyncio
async def test_unknown_comment_outcome_is_not_automatically_retried(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, write_enabled=True)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    posts = 0

    async def find_marker(_issue_key: str, _marker: str) -> str:
        return ""

    async def request(_method: str, _path: str, **_kwargs):
        nonlocal posts
        posts += 1
        raise httpx.ReadTimeout("unknown outcome")

    client._find_comment_marker = find_marker  # type: ignore[method-assign]
    client._request = request  # type: ignore[method-assign]
    try:
        first = await client.add_comment("PROJECT-123", "Analysis result", "op-1")
        repeated = await client.add_comment("PROJECT-123", "Analysis result", "op-1")
    finally:
        await client.close()

    assert first["status"] == "outcome_unknown"
    assert repeated["status"] == "outcome_unknown"
    assert first["retry_allowed"] is False
    assert posts == 1


@pytest.mark.asyncio
async def test_attachment_excerpt_rejects_same_host_url_with_userinfo(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))

    async def request(_method: str, _path: str, **_kwargs):
        return {
            "fields": {
                "attachment": [
                    {
                        "id": "attachment-1",
                        "filename": "agent.log",
                        "mimeType": "text/plain",
                        "size": 100,
                        "content": ("https://attacker@jira.example.test/attachment/1"),
                    }
                ]
            }
        }

    client._request = request  # type: ignore[method-assign]
    try:
        with pytest.raises(ValueError, match="outside the configured Jira origin"):
            await client.get_attachment_excerpt("PROJECT-123", "attachment-1")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reference_server_declares_standard_mcp_capabilities(
    tmp_path: Path,
) -> None:
    pytest.importorskip("starlette")
    from examples.jira_mcp_server.server import JiraMCPApplication

    app = JiraMCPApplication(_settings(tmp_path))
    options = app.initialization_options()
    try:
        assert options.capabilities.resources is not None
        assert options.capabilities.resources.subscribe is True
        assert options.capabilities.resources.list_changed is True
        assert options.capabilities.tools is not None
        assert options.capabilities.prompts is not None
    finally:
        await app.jira.close()


@pytest.mark.asyncio
async def test_reference_server_runs_over_real_stdio_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    environment = {
        "JIRA_BASE_URL": "http://127.0.0.1:1",
        "JIRA_USERNAME": "owner@example.test",
        "JIRA_API_TOKEN": "test-token",
        "JIRA_MCP_STATE_PATH": str(tmp_path / "stdio-jira.db"),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    config = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "examples.jira_mcp_server.server"],
            "working_directory": str(repo_root),
            "inherit_env": list(environment),
            "timeout_seconds": 5,
        }
    )
    client = StdioMCPClient(config)
    try:
        await client.start()
        resources = await client.list_resources()
        tools = await client.list_tools()
    finally:
        await client.close()

    assert [str(resource.uri) for resource in resources] == ["jira://assigned-to-me"]
    assert [tool.name for tool in tools] == [
        "jira.get_issue",
        "jira.get_comments",
        "jira.list_attachments",
        "jira.get_attachment_excerpt",
        "jira.add_comment",
    ]
