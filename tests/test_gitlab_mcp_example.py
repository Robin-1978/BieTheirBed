from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from mcp import types

from examples.gitlab_mcp_server.gitlab_client import (
    GitLabClient,
    GitLabSettings,
    GitLabStateStore,
)
from examples.gitlab_mcp_server.server import GitLabMCPApplication


def _settings(tmp_path: Path, *, actions_enabled: bool = False) -> GitLabSettings:
    return GitLabSettings(
        base_url="https://gitlab.example.test",
        token="secret",
        projects=("team/repo",),
        poll_interval_seconds=60,
        max_pipelines=50,
        retention_days=7,
        state_path=tmp_path / "gitlab.db",
        actions_enabled=actions_enabled,
    )


def test_gitlab_manifest_keeps_retry_behind_high_risk_approval() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).parents[1] / "examples/gitlab_mcp_server/mcp.yaml").read_text()
    )
    for name in ("gitlab.retry_job", "gitlab.retry_pipeline"):
        assert manifest["tools"][name] == {
            "effect": "external_side_effect",
            "capabilities": ["mcp", "network"],
            "risk": "high",
        }
    assert manifest["tools"]["gitlab.get_job_trace"]["effect"] == "read_only"


@pytest.mark.asyncio
async def test_first_failed_pipeline_poll_is_baseline_then_new_state_is_event(
    tmp_path: Path,
) -> None:
    client = GitLabClient(_settings(tmp_path), GitLabStateStore(tmp_path / "gitlab.db"))
    pipelines = [
        {
            "id": 7,
            "status": "failed",
            "sha": "abc",
            "ref": "main",
            "updated_at": "2026-08-13T01:00:00Z",
        }
    ]

    async def request(method: str, path: str, **kwargs):
        assert method == "GET"
        return list(pipelines)

    client._json = request  # type: ignore[method-assign]
    try:
        assert await client.poll_failure_events() == ()
        assert await client.poll_failure_events() == ()
        pipelines[0] = {**pipelines[0], "updated_at": "2026-08-13T01:10:00Z"}
        created = await client.poll_failure_events()
        repeated = await client.poll_failure_events()
    finally:
        await client.close()

    assert len(created) == 1
    assert created[0]["pipeline_id"] == "7"
    assert repeated == ()


@pytest.mark.asyncio
async def test_read_tools_are_allowlisted_and_trace_is_bounded(tmp_path: Path) -> None:
    client = GitLabClient(_settings(tmp_path), GitLabStateStore(tmp_path / "gitlab.db"))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/trace"):
            assert request.headers["range"] == "bytes=-1024"
            return httpx.Response(206, content=b"one\ntwo\nthree", request=request)
        return httpx.Response(200, json={"id": 9, "status": "failed"}, request=request)

    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://gitlab.example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert (await client.get_job("team/repo", "9"))["id"] == 9
        trace = await client.get_job_trace(
            "team/repo", "9", tail_lines=2, max_bytes=1024
        )
        with pytest.raises(ValueError, match="not configured"):
            await client.get_job("team/secret", "9")
        with pytest.raises(ValueError, match="positive integer"):
            await client.get_job("team/repo", "../9")
    finally:
        await client.close()

    assert trace["trace"] == "two\nthree"
    assert trace["truncated_by_bytes"] is True


@pytest.mark.asyncio
async def test_retry_is_idempotent_and_uses_job_endpoint(tmp_path: Path) -> None:
    client = GitLabClient(
        _settings(tmp_path, actions_enabled=True),
        GitLabStateStore(tmp_path / "gitlab.db"),
    )
    calls = []

    async def request(method: str, path: str, **kwargs):
        calls.append((method, path))
        if method == "GET":
            if path.endswith("/jobs/9"):
                return {
                    "id": 9,
                    "name": "build-x86",
                    "status": "failed",
                    "pipeline": {"id": 7},
                }
            return [
                {"id": 9, "name": "build-x86", "status": "failed"},
            ]
        return {"id": 9, "status": "pending"}

    client._json = request  # type: ignore[method-assign]
    try:
        first = await client.retry_job("team/repo", "9", "retry-1")
        replay = await client.retry_job("team/repo", "9", "retry-1")
        with pytest.raises(ValueError, match="conflicts"):
            await client.retry_pipeline("team/repo", "9", "retry-1")
    finally:
        await client.close()

    assert first == replay
    assert calls == [
        ("GET", "/api/v4/projects/team%2Frepo/jobs/9"),
        ("GET", "/api/v4/projects/team%2Frepo/pipelines/7/jobs"),
        ("POST", "/api/v4/projects/team%2Frepo/jobs/9/retry"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        "created",
        "pending",
        "preparing",
        "running",
        "waiting_for_resource",
        "scheduled",
        "canceling",
    ],
)
async def test_retry_job_rejects_active_job_state(
    tmp_path: Path,
    status: str,
) -> None:
    client = GitLabClient(
        _settings(tmp_path, actions_enabled=True),
        GitLabStateStore(tmp_path / "gitlab.db"),
    )
    calls: list[tuple[str, str]] = []

    async def request(method: str, path: str, **kwargs):
        calls.append((method, path))
        return {
            "id": 9,
            "name": "build-x86",
            "status": status,
            "pipeline": {"id": 7},
        }

    client._json = request  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="already active"):
            await client.retry_job("team/repo", "9", f"retry-{status}")
    finally:
        await client.close()

    assert calls == [("GET", "/api/v4/projects/team%2Frepo/jobs/9")]
    replay = GitLabClient(
        _settings(tmp_path, actions_enabled=True),
        GitLabStateStore(tmp_path / "gitlab.db"),
    )
    try:
        with pytest.raises(RuntimeError, match="already active"):
            await replay.retry_job("team/repo", "9", f"retry-{status}")
    finally:
        await replay.close()


@pytest.mark.asyncio
async def test_retry_job_rejects_newer_active_instance_with_same_name(
    tmp_path: Path,
) -> None:
    client = GitLabClient(
        _settings(tmp_path, actions_enabled=True),
        GitLabStateStore(tmp_path / "gitlab.db"),
    )
    calls: list[tuple[str, str]] = []

    async def request(method: str, path: str, **kwargs):
        calls.append((method, path))
        if path.endswith("/jobs/9"):
            return {
                "id": 9,
                "name": "build-x86",
                "status": "failed",
                "pipeline": {"id": 7},
            }
        return [
            {"id": 9, "name": "build-x86", "status": "failed"},
            {"id": 12, "name": "build-x86", "status": "running"},
            {"id": 13, "name": "build-arm", "status": "running"},
        ]

    client._json = request  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="job 12, status running"):
            await client.retry_job("team/repo", "9", "retry-active-copy")
    finally:
        await client.close()

    assert calls == [
        ("GET", "/api/v4/projects/team%2Frepo/jobs/9"),
        ("GET", "/api/v4/projects/team%2Frepo/pipelines/7/jobs"),
    ]


@pytest.mark.asyncio
async def test_mcp_exposes_resources_and_six_tools(tmp_path: Path) -> None:
    app = GitLabMCPApplication(_settings(tmp_path))
    result = await app._list_tools(None, None)
    names = {tool.name for tool in result.tools}
    assert names == {
        "gitlab.get_pipeline",
        "gitlab.list_pipeline_jobs",
        "gitlab.get_job",
        "gitlab.get_job_trace",
        "gitlab.retry_pipeline",
        "gitlab.retry_job",
    }
    app.store.add_failure_event(
        "source",
        "event-1",
        {
            "project": "team/repo",
            "pipeline_id": "7",
            "status": "failed",
            "sha": "abc",
            "ref": "main",
            "updated_at": "now",
            "web_url": "https://gitlab.example/pipelines/7",
        },
        3600,
    )
    resources = await app._list_resources(None, None)
    assert [str(resource.uri) for resource in resources.resources] == [
        "gitlab://failed-pipelines",
        "gitlab://failed-pipelines/events/event-1",
    ]
    instruction = await app._read_resource(
        None,
        types.ReadResourceRequestParams(
            uri="gitlab://failed-pipelines/events/event-1"
        ),
    )
    text = instruction.contents[0].text
    assert "total, succeeded, failed and skipped" in text
    assert "Runner memory pressure/OOM" in text
    assert "do not call shell or filesystem Tools" in text
    assert "local workspace" in text
    assert "confirm the target is failed or canceled" in text
    assert "no newer Job with the same name is active" in text
    assert "created, pending, preparing, running" in text
    assert "inspect the referenced branch" not in text
    await app.gitlab.close()
