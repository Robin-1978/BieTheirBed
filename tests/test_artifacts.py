from __future__ import annotations

import pytest

from pc_assistant.agent import Agent
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.attachments import AttachmentStore
from pc_assistant.config import AppConfig
from pc_assistant.llm_provider import StreamChunk
from pc_assistant.tools.screen import ScreenTool
from pc_assistant.tools.screenshot import ScreenshotTool


def test_artifact_store_copies_existing_file_and_hides_path(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    store = ArtifactStore(tmp_path / "attachments" / "artifacts")

    ref = store.prepare_path("session-a", source)

    assert ref["artifact_id"]
    assert ref["name"] == "report.txt"
    assert ref["media_type"] == "text/plain"
    assert "path" not in ref
    resolved = store.resolve("session-a", ref["artifact_id"])
    assert resolved["path"] != str(source)
    from pathlib import Path

    assert Path(resolved["path"]).read_text(encoding="utf-8") == "hello"

    with pytest.raises(KeyError):
        store.resolve("session-b", ref["artifact_id"])


def test_user_screenshot_schema_has_no_parameters(tmp_path):
    store = ArtifactStore(tmp_path / "attachments" / "artifacts")
    tool = ScreenshotTool(store, tmp_path / "attachments" / "screenshots")
    assert tool.schema()["parameters"]["properties"] == {}

    internal = ScreenTool().core_schema()
    assert set(internal["parameters"]["properties"]) == {"action"}
    assert "Not for user delivery" in internal["description"]


@pytest.mark.asyncio
async def test_agent_emits_core_artifact_event_without_server_path(tmp_path):
    source = tmp_path / "document.txt"
    source.write_text("deliver me", encoding="utf-8")
    agent = Agent(
        config=AppConfig(working_directory=str(tmp_path)),
        attachment_store=AttachmentStore(tmp_path / "attachments"),
    )
    calls = 0

    async def stream(messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield StreamChunk(delta_tool_calls=[{
                "id": "artifact-1",
                "type": "function",
                "function": {
                    "name": "artifact_prepare",
                    "arguments": {"path": "document.txt"},
                },
            }], finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_content="已准备文件。", finish_reason="stop")

    agent._llm.chat_stream = stream
    events = []
    async for event in agent.run("把 document.txt 给我", session_id="session-a"):
        events.append(event)

    artifacts = [event for event in events if event.type == "artifact"]
    assert len(artifacts) == 1
    public = artifacts[0].artifact
    assert public and public.name == "document.txt"
    assert "path" not in type(public).model_fields
    resolved = agent.resolve_artifact("session-a", public.artifact_id)
    assert resolved["path"].endswith("-document.txt")


def test_core_tools_explain_open_is_not_delivery(tmp_path):
    agent = Agent(
        config=AppConfig(),
        attachment_store=AttachmentStore(tmp_path / "attachments"),
    )
    schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in agent.registry.all_schemas()
    }
    assert "screenshot" in schemas
    assert "artifact_prepare" in schemas
    assert "current user" in schemas["artifact_prepare"]["description"]
