from __future__ import annotations

import pytest

from knoa_platform.agent_runtime.model_step import ProviderChunk
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.tools.base import ToolEffect
from knoa_platform.tools.image_inspect import ImageInspectTool
from knoa_platform.vision import VisionBroker


DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class VisionProvider:
    def __init__(self) -> None:
        self.requests = []

    def stream(self, request, cancellation):
        del cancellation

        async def iterate():
            self.requests.append(request)
            yield ProviderChunk(content_delta="A single white pixel is visible.")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


class EmptyThenVisionProvider(VisionProvider):
    def stream(self, request, cancellation):
        del cancellation

        async def iterate():
            self.requests.append(request)
            if len(self.requests) == 1:
                yield ProviderChunk(finish_reason="stop", terminal=True)
                return
            yield ProviderChunk(content_delta="The image contains a visible status panel.")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return iterate()


@pytest.mark.asyncio
async def test_vision_broker_returns_scoped_observation_and_caches_it(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "attachments", db_path=tmp_path / "data.db")
    ref = store.put_data_url("session-a", DATA_URL, name="photo.png")
    provider = VisionProvider()
    broker = VisionBroker(provider, store, model_alias="vision-a")

    first = await broker.inspect(
        "session-a",
        ref["artifact_id"],
        question="What pixels are visible?",
    )
    second = await broker.inspect(
        "session-a",
        ref["artifact_id"],
        question="What pixels are visible?",
    )

    assert first["observation"] == "A single white pixel is visible."
    assert first["model"] == "vision-a"
    assert first["cached"] is False
    assert first["retry_count"] == 0
    assert second["cached"] is True
    assert len(provider.requests) == 1
    assert provider.requests[0].max_output_tokens == 4096
    content = provider.requests[0].messages[-1]["content"]
    assert any(block.get("type") == "image" for block in content)


@pytest.mark.asyncio
async def test_vision_broker_retries_once_when_first_observation_is_empty(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "attachments", db_path=tmp_path / "data.db")
    ref = store.put_data_url("session-a", DATA_URL, name="photo.png")
    provider = EmptyThenVisionProvider()
    broker = VisionBroker(provider, store, model_alias="vision-a")

    result = await broker.inspect(
        "session-a",
        ref["artifact_id"],
        question="What is visibly present?",
    )

    assert result["observation"] == "The image contains a visible status panel."
    assert result["retry_count"] == 1
    assert len(provider.requests) == 2
    assert provider.requests[0].call_id != provider.requests[1].call_id


def test_image_inspect_tool_tracks_hot_vision_configuration(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "attachments", db_path=tmp_path / "data.db")
    broker = VisionBroker(None, store)
    tool = ImageInspectTool(broker)

    assert tool.policy.effect is ToolEffect.UNKNOWN

    broker.configure(VisionProvider(), model_alias="vision-a")
    assert tool.policy.effect is ToolEffect.READ_ONLY

    broker.configure(None)
    assert tool.policy.effect is ToolEffect.UNKNOWN
