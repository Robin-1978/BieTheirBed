from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pc_assistant.agent import AgentEvent
from pc_assistant.channels.feishu import FeishuChannel


class _ImageAgent:
    async def run(self, *args, **kwargs):
        yield AgentEvent(
            type="tool_result",
            tool_name="screen",
            tool_result={
                "success": True,
                "path": "/tmp/capture.png",
                "artifact": {
                    "kind": "image",
                    "path": "/tmp/capture.png",
                    "media_type": "image/png",
                    "temporary": True,
                },
            },
        )
        yield AgentEvent(type="final_answer", content="截图已发送")


class _PlainPathAgent:
    async def run(self, *args, **kwargs):
        yield AgentEvent(
            type="tool_result",
            tool_name="filesystem",
            tool_result={"path": "/tmp/not-an-image.txt"},
        )
        yield AgentEvent(type="final_answer", content="done")


@pytest.mark.asyncio
async def test_feishu_sends_declared_image_artifact():
    channel = FeishuChannel()
    channel._agent = _ImageAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "截屏发我")

    channel._send_image.assert_called_once_with("ou-user", "/tmp/capture.png")


@pytest.mark.asyncio
async def test_feishu_does_not_send_arbitrary_tool_paths_as_images():
    channel = FeishuChannel()
    channel._agent = _PlainPathAgent()
    channel._send_image = MagicMock(return_value=True)
    channel._send_card = MagicMock(return_value=True)

    await channel._process_with_agent_locked("ou-user", "read it")

    channel._send_image.assert_not_called()
