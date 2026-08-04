from __future__ import annotations

import re
import base64

import pytest

from pc_assistant.agent import Agent
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.config import AppConfig
from pc_assistant.context.scope import derive_memory_scope, reset_memory_scope, set_memory_scope
from pc_assistant.llm_provider import LLMResponse, StreamChunk
from pc_assistant.model_adapter.types import ImageAttachment
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.image_inspect import ImageInspectTool
from pc_assistant.vision.broker import VISION_SYSTEM_PROMPT, VisionBroker


class FakeVisionProvider:
    supports_vision = True

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return LLMResponse(
            content=(
                '{"description":"A dialog shows an error.",'
                '"visible_text":["WorkerError"],"entities":["dialog"],'
                '"regions":[],"confidence":0.94,"uncertainty":""}'
            ),
            finish_reason="stop",
        )


def _png(path):
    from PIL import Image

    Image.new("RGB", (20, 12), "white").save(path)


def _put_png(store, session_id, path):
    return store.put_data_url(
        session_id,
        "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode(),
    )


@pytest.mark.asyncio
async def test_broker_is_perception_only_and_never_returns_base64(tmp_path):
    image = tmp_path / "shot.png"
    _png(image)
    store = ArtifactStore(tmp_path / "attachments")
    ref = _put_png(store, "s1", image)
    provider = FakeVisionProvider()
    broker = VisionBroker(provider, store, model_name="qwen-vl")

    result = await broker.inspect("s1", ref["artifact_id"], focus="可见报错文字")

    assert result["visible_text"] == ["WorkerError"]
    assert result["model"] == "qwen-vl"
    assert "base64" not in str(result)
    assert "not a problem-solving assistant" in provider.calls[0][0]["content"]
    assert "Do not diagnose causes" in provider.calls[0][0]["content"]
    assert "data:image/png;base64," in str(provider.calls[0])


@pytest.mark.asyncio
async def test_broker_rejects_solution_question_and_caches_same_observation(tmp_path):
    image = tmp_path / "shot.png"
    _png(image)
    store = ArtifactStore(tmp_path / "attachments")
    ref = _put_png(store, "s1", image)
    provider = FakeVisionProvider()
    broker = VisionBroker(provider, store)

    with pytest.raises(ValueError, match="main model"):
        await broker.inspect("s1", ref["artifact_id"], focus="这个报错怎么解决？")

    first = await broker.inspect("s1", ref["artifact_id"], action="ocr", focus="报错是什么")
    second = await broker.inspect("s1", ref["artifact_id"], action="ocr", focus="报错是什么")
    third = await broker.inspect("s1", ref["artifact_id"], action="describe", focus="窗口布局")
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["observation_id"] == first["observation_id"]
    assert third["observation_id"] != first["observation_id"]
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_tool_uses_current_session_ownership(tmp_path):
    image = tmp_path / "shot.png"
    _png(image)
    store = ArtifactStore(tmp_path / "attachments")
    ref = _put_png(store, "session-a", image)
    tool = ImageInspectTool(VisionBroker(FakeVisionProvider(), store))

    token = set_memory_scope(derive_memory_scope("session-b"))
    try:
        result = await tool.execute(image_id=ref["artifact_id"])
    finally:
        reset_memory_scope(token)
    assert "error" in result


@pytest.mark.asyncio
async def test_text_main_gets_manifest_and_cannot_answer_before_observation(tmp_path):
    image = tmp_path / "shot.png"
    _png(image)
    store = ArtifactStore(tmp_path / "attachments")
    vision = FakeVisionProvider()
    agent = Agent(
        config=AppConfig(supports_vision=False, vision_enabled=True, max_iterations=4),
        artifact_store=store,
        vision_llm=vision,
    )
    main_calls: list[list[dict]] = []
    count = 0

    async def main_stream(messages, **kwargs):
        nonlocal count
        count += 1
        main_calls.append(messages)
        if count == 1:
            yield StreamChunk(delta_content="It is definitely a network failure.", finish_reason="stop")
            return
        if count == 2:
            match = re.search(r"image_id=([0-9a-f]+)", str(messages))
            assert match
            yield StreamChunk(delta_tool_calls=[{
                "id": "vision-1",
                "type": "function",
                "function": {
                    "name": "image_inspect",
                    "arguments": {"image_id": match.group(1), "action": "ocr", "focus": "可见报错是什么"},
                },
            }], finish_reason="tool_calls")
            return
        yield StreamChunk(delta_content="图片中可见 WorkerError；基于该证据再由主模型分析。", finish_reason="stop")

    agent._llm.chat_stream = main_stream
    events = []
    async for event in agent.run(
        "这个截图里的报错是什么？",
        session_id="session-a",
        attachments=[ImageAttachment.from_path(image)],
    ):
        events.append(event)

    assert all("data:image" not in str(call) for call in main_calls)
    assert not any("network failure" in event.content for event in events)
    assert any(event.tool_name == "image_inspect" and event.type == "tool_result" for event in events)
    assert any(event.type == "final_answer" and "WorkerError" in event.content for event in events)
    assert vision.calls


def test_image_inspect_schema_has_bounded_observation_operations():
    schema = ImageInspectTool.schema(object.__new__(ImageInspectTool))
    actions = schema["parameters"]["properties"]["action"]["enum"]
    assert actions == ["describe", "ocr", "locate", "compare"]
    assert "solve" not in str(schema).lower()
    assert "do not diagnose causes" in VISION_SYSTEM_PROMPT.lower()
    assert "do not propose fixes" in VISION_SYSTEM_PROMPT.lower()


def test_multimodal_main_does_not_register_fallback_vision_tool(tmp_path):
    class MustNotBeUsed:
        @property
        def supports_vision(self):
            raise AssertionError("fallback vision provider must not be constructed or inspected")

    agent = Agent(
        config=AppConfig(supports_vision=True, vision_enabled=True),
        artifact_store=ArtifactStore(tmp_path / "attachments"),
        vision_llm=MustNotBeUsed(),
    )

    assert "image_inspect" not in agent.registry.list_tools()
    assert agent._vision_broker is None
    assert all(
        schema["function"]["name"] != "image_inspect"
        for schema in agent.registry.all_schemas()
    )


@pytest.mark.asyncio
async def test_text_main_must_inspect_tool_generated_screenshot(tmp_path):
    image = tmp_path / "screen.png"
    _png(image)
    data_url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()

    class ScreenshotTool(ToolBase):
        name = "test_screenshot"
        description = "Capture a test screenshot"

        async def execute(self, **kwargs):
            return {
                "path": str(tmp_path / "not-managed.png"),
                "image": {"type": "image", "media_type": "image/png", "image_url": data_url},
            }

        def schema(self):
            return {"name": self.name, "parameters": {"type": "object", "properties": {}}}

    vision = FakeVisionProvider()
    agent = Agent(
        config=AppConfig(supports_vision=False, vision_enabled=True, max_iterations=4),
        artifact_store=ArtifactStore(tmp_path / "attachments"),
        vision_llm=vision,
    )
    agent.register_tool(ScreenshotTool())
    calls = 0

    async def main_stream(messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield StreamChunk(delta_tool_calls=[{
                "id": "screen-1",
                "type": "function",
                "function": {"name": "test_screenshot", "arguments": {}},
            }], finish_reason="tool_calls")
        elif calls == 2:
            match = re.search(r"image_id=([0-9a-f]+)", str(messages))
            assert match
            yield StreamChunk(delta_tool_calls=[{
                "id": "vision-1",
                "type": "function",
                "function": {
                    "name": "image_inspect",
                    "arguments": {"image_id": match.group(1), "focus": "描述可见内容"},
                },
            }], finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_content="截图中可见一个错误对话框。", finish_reason="stop")

    agent._llm.chat_stream = main_stream
    events = []
    async for event in agent.run("截屏并告诉我画面内容", session_id="screen-session"):
        events.append(event)

    assert any(event.tool_name == "image_inspect" for event in events)
    assert any(event.type == "final_answer" for event in events)
    assert vision.calls
