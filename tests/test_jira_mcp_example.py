from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from examples.jira_mcp_server.jira_client import (
    JiraClient,
    JiraSettings,
    JiraStateStore,
)
from knoa_platform.extensions.mcp import StdioMCPClient
from knoa_platform.extensions.models import MCPServerConfig


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
        attachment_root=tmp_path / "evidence",
        code_root=None,
        log_root=None,
        analysis_prompt_path=None,
        max_attachment_bytes=1024 * 1024,
        write_enabled=write_enabled,
    )


def _attachment_issue(
    *,
    attachment_id: str = "attachment-1",
    filename: str = "agent.log",
    mime_type: str = "text/plain",
    size: int = 4,
) -> dict:
    return {
        "fields": {
            "attachment": [
                {
                    "id": attachment_id,
                    "filename": filename,
                    "mimeType": mime_type,
                    "size": size,
                    "content": f"https://jira.example.test/attachment/{attachment_id}",
                }
            ]
        }
    }


def _stream_response(body: bytes, *, content_type: str = "application/octet-stream"):
    return httpx.Response(
        200,
        headers={"content-length": str(len(body)), "content-type": content_type},
        content=body,
        request=httpx.Request("GET", "https://jira.example.test/attachment/1"),
    )


def test_jira_write_tools_are_host_confirmation_gated() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "examples/jira_mcp_server/mcp.yaml").read_text(
            encoding="utf-8"
        )
    )
    tools = manifest["tools"]
    for name in ("jira.add_comment", "jira.assign_issue", "jira.transition_issue"):
        assert tools[name] == {
            "effect": "external_side_effect",
            "capabilities": ["mcp", "network"],
            "risk": "high",
        }
    assert tools["jira.find_assignable_users"]["effect"] == "read_only"
    assert tools["jira.list_transitions"]["effect"] == "read_only"


def test_bearer_settings_do_not_require_username(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
    monkeypatch.setenv("JIRA_AUTH_MODE", "bearer")
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.setenv("JIRA_MCP_STATE_PATH", str(tmp_path / "jira.db"))
    monkeypatch.setenv("JIRA_ATTACHMENT_ROOT", str(tmp_path / "evidence"))

    settings = JiraSettings.from_env()

    assert settings.auth_mode == "bearer"
    assert settings.username == ""


def test_basic_settings_require_username(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
    monkeypatch.setenv("JIRA_AUTH_MODE", "basic")
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.setenv("JIRA_MCP_STATE_PATH", str(tmp_path / "jira.db"))
    monkeypatch.setenv("JIRA_ATTACHMENT_ROOT", str(tmp_path / "evidence"))

    with pytest.raises(ValueError, match="JIRA_USERNAME"):
        JiraSettings.from_env()


def test_analysis_paths_and_prompt_are_operator_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code_root = tmp_path / "code"
    log_root = tmp_path / "logs"
    prompt = tmp_path / "analyze.md"
    code_root.mkdir()
    log_root.mkdir()
    prompt.write_text("Correlate exact timestamps with source symbols.", encoding="utf-8")
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
    monkeypatch.setenv("JIRA_AUTH_MODE", "bearer")
    monkeypatch.setenv("JIRA_MCP_STATE_PATH", str(tmp_path / "jira.db"))
    monkeypatch.setenv("JIRA_ATTACHMENT_ROOT", str(tmp_path / "evidence"))
    monkeypatch.setenv("JIRA_CODE_ROOT", str(code_root))
    monkeypatch.setenv("JIRA_LOG_ROOT", str(log_root))
    monkeypatch.setenv("JIRA_ANALYSIS_PROMPT_PATH", str(prompt))

    settings = JiraSettings.from_env()

    assert settings.code_root == code_root
    assert settings.log_root == log_root
    assert settings.analysis_prompt_path == prompt
    assert settings.analysis_instructions() == (
        "Correlate exact timestamps with source symbols."
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

    issues = [
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
            }
    ]

    async def search_assigned_issues():
        return tuple(issues)

    async def materialize(issue_key: str):
        evidence = settings.attachment_root / issue_key
        evidence.mkdir(parents=True)
        manifest = evidence / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return {"evidence_directory": str(evidence), "manifest": str(manifest)}

    client.current_user_ids = current_user_ids  # type: ignore[method-assign]
    client.search_assigned_issues = search_assigned_issues  # type: ignore[method-assign]
    client.materialize_issue = materialize  # type: ignore[method-assign]
    try:
        baseline = await client.poll_assignment_events()
        repeated_baseline = await client.poll_assignment_events()
        issues.append(
            {
                "id": "10002",
                "key": "PROJECT-124",
                "fields": {"created": "2026-08-13T00:00:00.000+0000"},
                "changelog": {
                    "histories": [
                        {
                            "id": "9002",
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
            }
        )
        first = await client.poll_assignment_events()
        repeated = await client.poll_assignment_events()
    finally:
        await client.close()

    assert baseline == ()
    assert repeated_baseline == ()
    assert len(first) == 1
    assert first[0]["issue_key"] == "PROJECT-124"
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
async def test_find_assignable_users_uses_exact_ids_for_later_assignment(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))

    async def request(method: str, path: str, **kwargs):
        assert method == "GET"
        assert path.endswith("/user/assignable/search")
        assert kwargs["params"] == {
            "issueKey": "PROJECT-123",
            "username": "Zhang San",
            "maxResults": 20,
        }
        return [
            {
                "name": "zhangsan",
                "displayName": "张三",
                "emailAddress": "zhangsan@example.test",
            }
        ]

    client._request = request  # type: ignore[method-assign]
    try:
        users = await client.find_assignable_users("PROJECT-123", "Zhang San")
    finally:
        await client.close()

    assert users == (
        {
            "id": "zhangsan",
            "display_name": "张三",
            "email": "zhangsan@example.test",
        },
    )


@pytest.mark.asyncio
async def test_assign_issue_requires_write_switch_and_uses_jira_identity_field(
    tmp_path: Path,
) -> None:
    disabled = JiraClient(_settings(tmp_path), JiraStateStore(tmp_path / "disabled.db"))
    try:
        with pytest.raises(PermissionError, match="disabled"):
            await disabled.assign_issue("PROJECT-123", "zhangsan")
    finally:
        await disabled.close()

    settings = _settings(tmp_path, write_enabled=True)
    client = JiraClient(settings, JiraStateStore(settings.state_path))

    async def request(method: str, path: str, **kwargs):
        assert method == "PUT"
        assert path.endswith("/issue/PROJECT-123/assignee")
        assert kwargs["json"] == {"name": "zhangsan"}

    client._request = request  # type: ignore[method-assign]
    try:
        result = await client.assign_issue("PROJECT-123", "zhangsan")
    finally:
        await client.close()

    assert result["status"] == "succeeded"
    assert result["assignee_id"] == "zhangsan"


@pytest.mark.asyncio
async def test_list_and_apply_transition_use_fields_discovered_from_jira(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, write_enabled=True)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    calls: list[tuple[str, str, dict]] = []

    async def request(method: str, path: str, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {
                "transitions": [
                    {
                        "id": "71",
                        "name": "提交验证",
                        "to": {"name": "待验证"},
                        "fields": {
                            "customfield_1": {
                                "name": "责任部门",
                                "required": False,
                                "schema": {"type": "option"},
                                "allowedValues": [{"id": "2", "value": "研发"}],
                            }
                        },
                    }
                ]
            }
        return None

    client._request = request  # type: ignore[method-assign]
    try:
        transitions = await client.list_transitions("PROJECT-123")
        result = await client.transition_issue(
            "PROJECT-123",
            "71",
            fields={"customfield_1": {"id": "2"}},
        )
    finally:
        await client.close()

    assert transitions[0]["target_status"] == "待验证"
    assert transitions[0]["fields"]["customfield_1"]["allowed_values"] == (
        {"id": "2", "name": "研发"},
    )
    assert calls[0][2]["params"] == {"expand": "transitions.fields"}
    assert calls[1][0] == "GET"
    assert calls[2][2]["json"] == {
        "transition": {"id": "71"},
        "fields": {"customfield_1": {"id": "2"}},
    }
    assert result["status"] == "succeeded"


@pytest.mark.asyncio
async def test_transition_rejects_unknown_fields_before_write(tmp_path: Path) -> None:
    settings = _settings(tmp_path, write_enabled=True)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    posts = 0

    async def request(method: str, _path: str, **_kwargs):
        nonlocal posts
        if method == "POST":
            posts += 1
        return {
            "transitions": [
                {"id": "71", "name": "提交验证", "fields": {}}
            ]
        }

    client._request = request  # type: ignore[method-assign]
    try:
        with pytest.raises(ValueError, match="not currently available"):
            await client.transition_issue(
                "PROJECT-123",
                "71",
                fields={"customfield_unknown": "value"},
            )
    finally:
        await client.close()

    assert posts == 0


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
async def test_download_attachment_sanitizes_filename_and_reuses_verified_file(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    body = b"logs"
    requests = 0

    async def request(_method: str, _path: str, **_kwargs):
        return _attachment_issue(filename="../../robot log.txt", size=len(body))

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return _stream_response(body, content_type="text/plain")

    client._request = request  # type: ignore[method-assign]
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await client.download_attachment("PROJECT-123", "attachment-1")
        repeated = await client.download_attachment("PROJECT-123", "attachment-1")
    finally:
        await client.close()

    path = Path(first["path"])
    assert path == settings.attachment_root / "PROJECT-123/attachments/attachment-1-robot_log.txt"
    assert path.read_bytes() == body
    assert first["reused"] is False
    assert repeated["reused"] is True
    assert requests == 1
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_download_attachment_rejects_stream_over_limit_and_leaves_no_partial(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings = JiraSettings(
        **{**settings.__dict__, "max_attachment_bytes": 4}
    )
    client = JiraClient(settings, JiraStateStore(settings.state_path))

    async def request(_method: str, _path: str, **_kwargs):
        return _attachment_issue(size=0)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _stream_response(b"12345")

    client._request = request  # type: ignore[method-assign]
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="exceeds"):
            await client.download_attachment("PROJECT-123", "attachment-1")
    finally:
        await client.close()

    attachment_dir = settings.attachment_root / "PROJECT-123/attachments"
    assert not list(attachment_dir.glob("*"))


@pytest.mark.asyncio
async def test_download_attachment_rejects_fake_image_content(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    body = b"not-an-image"

    async def request(_method: str, _path: str, **_kwargs):
        return _attachment_issue(
            filename="error.png",
            mime_type="image/png",
            size=len(body),
        )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _stream_response(body, content_type="image/png")

    client._request = request  # type: ignore[method-assign]
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="image attachment content is invalid"):
            await client.download_attachment("PROJECT-123", "attachment-1")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_attachment_rejects_image_mime_format_mismatch(
    tmp_path: Path,
) -> None:
    from PIL import Image

    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    image_path = tmp_path / "actual.png"
    Image.new("RGB", (2, 2), color="red").save(image_path, format="PNG")
    body = image_path.read_bytes()

    async def request(_method: str, _path: str, **_kwargs):
        return _attachment_issue(
            filename="claimed.jpg",
            mime_type="image/jpeg",
            size=len(body),
        )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _stream_response(body, content_type="image/jpeg")

    client._request = request  # type: ignore[method-assign]
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="does not match"):
            await client.download_attachment("PROJECT-123", "attachment-1")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_attachment_rejects_symlink_target(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    body = b"logs"
    attachment_dir = settings.attachment_root / "PROJECT-123/attachments"
    attachment_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    target = attachment_dir / "attachment-1-agent.log"
    target.symlink_to(outside)

    async def request(_method: str, _path: str, **_kwargs):
        return _attachment_issue(size=len(body))

    client._request = request  # type: ignore[method-assign]
    try:
        with pytest.raises(ValueError, match="symbolic link"):
            await client.download_attachment("PROJECT-123", "attachment-1")
    finally:
        await client.close()

    assert outside.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_materialize_issue_writes_complete_manifest(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = JiraClient(settings, JiraStateStore(settings.state_path))
    body = b"log-data"

    async def get_issue(_issue_key: str, *, changelog: bool = False):
        return {"key": "PROJECT-123", "summary": "Failure", "changelog": changelog}

    async def get_comments(_issue_key: str, *, limit: int = 50):
        assert limit == 100
        return ({"id": "comment-1", "body": "Observed failure"},)

    async def attachment_records(_issue_key: str):
        return tuple(_attachment_issue(size=len(body))["fields"]["attachment"])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _stream_response(body, content_type="text/plain")

    client.get_issue = get_issue  # type: ignore[method-assign]
    client.get_comments = get_comments  # type: ignore[method-assign]
    client._attachment_records = attachment_records  # type: ignore[method-assign]
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.materialize_issue("PROJECT-123")
    finally:
        await client.close()

    evidence = Path(result["evidence_directory"])
    manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    assert json.loads((evidence / "issue.json").read_text(encoding="utf-8"))["summary"] == "Failure"
    assert json.loads((evidence / "comments.json").read_text(encoding="utf-8"))["comments"][0]["id"] == "comment-1"
    assert manifest["format"] == "knoa-jira-evidence-v1"
    assert manifest["attachments"][0]["path"].endswith("attachment-1-agent.log")
    assert result["attachment_count"] == 1


@pytest.mark.asyncio
async def test_assignment_event_is_published_only_after_materialization(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = JiraStateStore(settings.state_path)
    client = JiraClient(settings, store)
    attempts = 0

    async def current_user_ids():
        return {"owner"}

    issues: list[dict[str, Any]] = []

    async def search_assigned_issues():
        return tuple(issues)

    assigned_issue = (
            {
                "id": "10001",
                "key": "PROJECT-123",
                "fields": {"created": "2026-08-12"},
                "changelog": {"histories": []},
            }
    )

    async def materialize(_issue_key: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("attachment unavailable")
        evidence = settings.attachment_root / "PROJECT-123"
        evidence.mkdir(parents=True)
        manifest = evidence / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        return {"evidence_directory": str(evidence), "manifest": str(manifest)}

    client.current_user_ids = current_user_ids  # type: ignore[method-assign]
    client.search_assigned_issues = search_assigned_issues  # type: ignore[method-assign]
    client.materialize_issue = materialize  # type: ignore[method-assign]
    try:
        assert await client.poll_assignment_events() == ()
        issues.append(assigned_issue)
        assert await client.poll_assignment_events() == ()
        assert store.list_assignment_events() == ()
        created = await client.poll_assignment_events()
    finally:
        await client.close()

    assert len(created) == 1
    assert created[0]["evidence_directory"].endswith("PROJECT-123")
    assert len(store.list_assignment_events()) == 1


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
async def test_assignment_resource_points_agent_to_materialized_directory(
    tmp_path: Path,
) -> None:
    pytest.importorskip("starlette")
    from examples.jira_mcp_server.server import JiraMCPApplication

    prompt = tmp_path / "analyze.md"
    prompt.write_text("First instruction", encoding="utf-8")
    settings = _settings(tmp_path)
    settings = JiraSettings(
        **{
            **settings.__dict__,
            "code_root": tmp_path / "code",
            "log_root": tmp_path / "logs",
            "analysis_prompt_path": prompt,
        }
    )
    app = JiraMCPApplication(settings)
    event_id = "event-1"
    evidence = settings.attachment_root / "PROJECT-123"
    evidence.mkdir(parents=True)
    manifest = evidence / "manifest.json"
    manifest.write_text('{"format":"knoa-jira-evidence-v1"}', encoding="utf-8")
    app.store.add_assignment_event(
        event_id,
        "PROJECT-123",
        retention_seconds=3600,
    )

    class Context:
        session = object()
        protocol_version = "2025-06-18"

    try:
        result = await app._read_resource(
            Context(),
            type("Params", (), {"uri": f"jira://assigned-to-me/events/{event_id}"})(),
        )
    finally:
        await app.jira.close()

    text = result.contents[0].text
    assert f"Evidence and downloaded Jira logs: {evidence}" in text
    assert f"Evidence manifest: {manifest}" in text
    assert f"Source code root: {tmp_path / 'code'}" in text
    assert f"Additional local log root: {tmp_path / 'logs'}" in text
    assert "First instruction" in text

    prompt.write_text("Updated without restart", encoding="utf-8")
    refreshed = await app._read_resource(
        Context(),
        type("Params", (), {"uri": f"jira://assigned-to-me/events/{event_id}"})(),
    )
    assert "Updated without restart" in refreshed.contents[0].text


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
        "JIRA_ATTACHMENT_ROOT": str(tmp_path / "evidence"),
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
        "jira.download_attachment",
        "jira.materialize_issue",
        "jira.get_comments",
        "jira.find_assignable_users",
        "jira.list_attachments",
        "jira.get_attachment_excerpt",
        "jira.add_comment",
        "jira.assign_issue",
        "jira.list_transitions",
        "jira.transition_issue",
    ]
