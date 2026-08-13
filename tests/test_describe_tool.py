from __future__ import annotations

import pytest

from knoa_platform.tools.describe_tool import DescribeTool
from knoa_platform.tools.registry import ToolRegistry
from knoa_platform.tools.weather import WeatherTool
from knoa_platform.tools.web_search import WebSearchTool


@pytest.mark.asyncio
async def test_tool_help_describes_an_exact_tool_name() -> None:
    registry = ToolRegistry()
    registry.register(WeatherTool())
    tool = DescribeTool(registry)

    result = await tool.execute(tool_name="weather")

    assert result["found"] is True
    assert result["tool"] == "weather"
    assert result["schema"]["inputSchema"]["required"] == ["location"]


@pytest.mark.asyncio
async def test_tool_help_unknown_name_is_a_successful_discovery_result() -> None:
    registry = ToolRegistry()
    registry.register(WebSearchTool())
    tool = DescribeTool(registry)

    result = await tool.execute(tool_name="search")

    assert result["found"] is False
    assert result["suggestions"] == ["web_search"]
    assert result["available_tools"] == ["web_search"]
    assert "error" not in result


@pytest.mark.asyncio
async def test_tool_help_searches_names_and_descriptions() -> None:
    registry = ToolRegistry()
    registry.register(WebSearchTool())
    tool = DescribeTool(registry)

    result = await tool.execute(query="web search")

    assert result["found"] is False
    assert result["query"] == "web search"
    assert result["matches"][0]["name"] == "web_search"
    assert result["available_tools"] == ["web_search"]
