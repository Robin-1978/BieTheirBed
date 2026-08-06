from __future__ import annotations

import pytest

from pc_assistant.agent import Agent
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.config import AppConfig
from pc_assistant.llm_provider import StreamChunk
from pc_assistant.tools.screen import ScreenTool
from pc_assistant.tools.screenshot import ScreenshotTool


def test_artifact_store_borrows_existing_file_and_never_deletes_source(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("hello", encoding="utf-8")
    store = ArtifactStore(tmp_path / "attachments", ttl_seconds=10, clock=lambda: 100.0)

    ref = store.prepare_path("session-a", source)

    assert ref["artifact_id"]
    assert ref["name"] == "report.txt"
    assert ref["media_type"] == "text/plain"
    assert ref["ownership"] == "borrowed"
    assert "path" not in ref
    resolved = store.resolve("session-a", ref["artifact_id"])
    assert resolved["path"] == str(source)

    store.cleanup_session("session-a")
    assert source.read_text(encoding="utf-8") == "hello"

    with pytest.raises(KeyError):
        store.resolve("session-b", ref["artifact_id"])


def test_borrowed_file_survives_artifact_expiry(tmp_path):
    now = [100.0]
    source = tmp_path / "report.txt"
    source.write_text("keep me", encoding="utf-8")
    store = ArtifactStore(
        tmp_path / "attachments",
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    ref = store.prepare_path("session-a", source)

    now[0] = 111.0
    store.cleanup_expired()

    assert source.read_text(encoding="utf-8") == "keep me"
    with pytest.raises(KeyError):
        store.resolve("session-a", ref["artifact_id"])


def test_delivered_temporary_generated_artifact_uses_grace_period(tmp_path):
    now = [100.0]
    root = tmp_path / "attachments"
    generated = root / "screenshots" / "capture.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"png")
    store = ArtifactStore(
        root,
        ttl_seconds=100,
        delivery_grace_seconds=5,
        clock=lambda: now[0],
    )
    ref = store.register_generated("session-a", generated, media_type="image/png")

    delivered = store.mark_delivered("session-a", ref["artifact_id"])
    assert delivered["status"] == "delivered"
    now[0] = 106.0
    store.cleanup_expired()

    assert not generated.exists()


def test_persistent_generated_artifact_survives_session_and_store_restart(tmp_path):
    root = tmp_path / "attachments"
    persistent_root = tmp_path / "artifacts"
    db_path = tmp_path / "data" / "assistant.db"
    generated = persistent_root / "report.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("durable", encoding="utf-8")
    store = ArtifactStore(root, persistent_root=persistent_root, db_path=db_path)
    ref = store.register_generated(
        "session-a",
        generated,
        retention="persistent",
    )

    store.cleanup_session("session-a")
    reopened = ArtifactStore(root, persistent_root=persistent_root, db_path=db_path)
    resolved = reopened.resolve("session-a", ref["artifact_id"])

    assert resolved["path"] == str(generated)
    assert generated.read_text(encoding="utf-8") == "durable"


def test_user_screenshot_schema_has_no_parameters(tmp_path):
    store = ArtifactStore(tmp_path / "attachments")
    tool = ScreenshotTool(store, tmp_path / "attachments" / "screenshots")
    assert tool.schema()["parameters"]["properties"] == {}

    internal = ScreenTool().schema()
    assert "action" in internal["parameters"]["properties"]
    assert internal["parameters"]["properties"]["action"]["enum"] == ["look", "verify", "info"]


@pytest.mark.asyncio
async def test_agent_emits_core_artifact_event_without_server_path(tmp_path):
    source = tmp_path / "document.txt"
    source.write_text("deliver me", encoding="utf-8")
    agent = Agent(
        config=AppConfig(working_directory=str(tmp_path)),
        artifact_store=ArtifactStore(tmp_path / "attachments"),
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
                    "name": "attach",
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
    assert resolved["path"] == str(source)
    assert public.ownership == "borrowed"


def test_core_tools_explain_open_is_not_delivery(tmp_path):
    agent = Agent(
        config=AppConfig(),
        artifact_store=ArtifactStore(tmp_path / "attachments"),
    )
    schemas = {
        schema["function"]["name"]: schema["function"]
        for schema in agent.registry.all_schemas()
    }
    assert "screenshot" in schemas
    assert "attach" in schemas
    assert "Attach" in schemas["attach"]["description"]
