"""Tests for the cloud_files MCP reference server (OSS + Tempo evidence)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from examples.cloud_files_mcp_server import cloud_client
from examples.cloud_files_mcp_server.cloud_client import (
    CloudFilesClient,
    CloudFilesSettings,
)


def _settings(tmp_path: Path) -> CloudFilesSettings:
    return CloudFilesSettings(download_root=tmp_path / "cloud")


def test_list_tools_exposes_two_tools() -> None:
    import asyncio

    from examples.cloud_files_mcp_server.server import CloudFilesMCPApplication

    app = CloudFilesMCPApplication(_settings(Path("/tmp/does-not-matter")))

    async def run():
        return await app._list_tools(None, None)

    result = asyncio.run(run())
    assert [tool.name for tool in result.tools] == [
        "cloud_files.list_files",
        "cloud_files.download_file",
    ]


@pytest.mark.asyncio
async def test_list_files_rejects_unknown_source(tmp_path: Path) -> None:
    client = CloudFilesClient(_settings(tmp_path))
    with pytest.raises(ValueError, match="source must be"):
        await client.list_files("jira")


@pytest.mark.asyncio
async def test_list_files_tempo_requires_share_link(tmp_path: Path) -> None:
    client = CloudFilesClient(_settings(tmp_path))
    with pytest.raises(ValueError, match="share_id and sn"):
        await client.list_files("tempo", share_id="", sn="")


@pytest.mark.asyncio
async def test_list_and_download_oss_roundtrip(tmp_path: Path) -> None:
    listing_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<ListBucketResult>"
        "<Contents><Key>logs/a.log</Key><Size>3</Size></Contents>"
        "</ListBucketResult>"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=listing_xml)
        return httpx.Response(200, content=b"abc")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        monkey_orig_list = cloud_client.list_oss_objects
        monkey_orig_dl = cloud_client.download_oss_file
        cloud_client.list_oss_objects = lambda prefix, limit=100: monkey_orig_list(  # type: ignore[method-assign]
            prefix, limit=limit, client=mock_http
        )

        async def fake_download(url, dest, max_bytes=500 * 1024 * 1024):
            return await monkey_orig_dl(url, dest, max_bytes=max_bytes, client=mock_http)

        cloud_client.download_oss_file = fake_download  # type: ignore[method-assign]
        try:
            client = CloudFilesClient(_settings(tmp_path))
            listed = await client.list_files("oss", prefix="logs/")
            assert listed["objects"][0]["path"] == "oss://gs-public-shared/logs/a.log"
            downloaded = await client.download_file(
                "oss", "oss://gs-public-shared/logs/a.log", case_id="case-1"
            )
            assert downloaded["case_id"] == "case-1"
            assert Path(downloaded["path"]).read_bytes() == b"abc"
        finally:
            cloud_client.list_oss_objects = monkey_orig_list  # type: ignore[method-assign]
            cloud_client.download_oss_file = monkey_orig_dl  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_list_and_download_tempo_roundtrip(tmp_path: Path) -> None:
    from examples.cloud_files_mcp_server import tempo_files

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/records"):
            return httpx.Response(
                200,
                json={"records": [{"id": "r1", "fileName": "a.bag", "size": 3}]},
            )
        return httpx.Response(200, content=b"xyz")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        orig_list = cloud_client.list_tempo_records
        orig_dl = cloud_client.download_tempo_file

        async def fake_list(*args: object, **kwargs: object):
            return await orig_list(*args, **kwargs, client=mock_http)  # type: ignore[arg-type]

        async def fake_dl(*args: object, **kwargs: object):
            return await orig_dl(*args, **kwargs, client=mock_http)  # type: ignore[arg-type]

        cloud_client.list_tempo_records = fake_list  # type: ignore[method-assign]
        cloud_client.download_tempo_file = fake_dl  # type: ignore[method-assign]
        try:
            client = CloudFilesClient(_settings(tmp_path))
            listed = await client.list_files("tempo", share_id="sid", sn="TBPR1")
            assert listed["records"][0]["fileName"] == "a.bag"
            downloaded = await client.download_file(
                "tempo", "https://example.test/f", filename="a.bag", case_id="case-2"
            )
            assert Path(downloaded["path"]).read_bytes() == b"xyz"
        finally:
            cloud_client.list_tempo_records = orig_list  # type: ignore[method-assign]
            cloud_client.download_tempo_file = orig_dl  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_download_file_rejects_bad_source(tmp_path: Path) -> None:
    client = CloudFilesClient(_settings(tmp_path))
    with pytest.raises(ValueError, match="source must be"):
        await client.download_file("jira", "https://example.test/f")
