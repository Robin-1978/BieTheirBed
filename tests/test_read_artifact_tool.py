from __future__ import annotations

import base64

import pytest

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.context.scope import MemoryScope, reset_memory_scope, set_memory_scope
from pc_assistant.tools.read_artifact import ReadArtifactTool


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
