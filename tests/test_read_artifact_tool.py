from __future__ import annotations

import base64

import pytest

from knoa_platform.artifacts import ArtifactStore
from knoa_platform.context.scope import MemoryScope, reset_memory_scope, set_memory_scope
from knoa_platform.tools.read_artifact import ReadArtifactTool


@pytest.mark.asyncio
async def test_read_artifact_reads_only_current_session_text(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "attachments")
    encoded = base64.b64encode("项目进展正常".encode()).decode()
    ref = store.put_data_url(
        "session-a",
        f"data:text/plain;base64,{encoded}",
        name="status.txt",
    )
    tool = ReadArtifactTool(store)
    token = set_memory_scope(
        MemoryScope(principal_id="principal-a", session_id="session-a")
    )
    try:
        result = await tool.execute(artifact_id=ref["artifact_id"])
    finally:
        reset_memory_scope(token)

    assert result["name"] == "status.txt"
    assert result["content"] == "项目进展正常"

    foreign = set_memory_scope(
        MemoryScope(principal_id="principal-b", session_id="session-b")
    )
    try:
        rejected = await tool.execute(artifact_id=ref["artifact_id"])
    finally:
        reset_memory_scope(foreign)
    assert "error" in rejected


@pytest.mark.asyncio
async def test_read_artifact_supports_offset_and_limit_pagination(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "attachments")
    multi_line = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    created = store.create_generated_text(
        "session-p",
        multi_line,
        name="log.txt",
    )
    tool = ReadArtifactTool(store)
    token = set_memory_scope(
        MemoryScope(principal_id="principal-p", session_id="session-p")
    )
    try:
        page = await tool.execute(
            artifact_id=created["artifact_id"],
            offset=2,
            limit=2,
        )
    finally:
        reset_memory_scope(token)

    assert page["content"] == "line 2\nline 3\n"
    assert page["showing"] == "lines 2-3 of 5"
    assert page["total_lines"] == 5
    assert page["has_more"] is True


@pytest.mark.asyncio
async def test_read_artifact_resolves_cross_layer_bound_session(tmp_path) -> None:
    db_path = tmp_path / "assistant.db"
    store = ArtifactStore(tmp_path / "attachments", db_path=db_path)
    created = store.create_generated_text(
        "runtime-ref-123",
        "cross-layer content line 1\nline 2",
        name="tool_res.txt",
    )
    with store._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_session_bindings (
                session_handle TEXT,
                runtime_session_ref TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO agent_session_bindings VALUES (?, ?)",
            ("session-handle-456", "runtime-ref-123"),
        )

    tool = ReadArtifactTool(store)
    token = set_memory_scope(
        MemoryScope(principal_id="personal:owner", session_id="session-handle-456")
    )
    try:
        res = await tool.execute(artifact_id=created["artifact_id"])
    finally:
        reset_memory_scope(token)

    assert "cross-layer content" in res["content"]


