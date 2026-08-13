from __future__ import annotations

import pytest

from knoa_platform.tools.exchange import ExchangeTool
from knoa_platform.tools.weather import WeatherTool
from knoa_platform.tools.web_fetch import WebFetchTool
from knoa_platform.tools.web_search import WebSearchTool


@pytest.mark.asyncio
async def test_user_controlled_network_inputs_are_bounded_before_http() -> None:
    assert await WebFetchTool().execute(url="https://example.com/" + "x" * 4096) == {
        "error": "url must contain at most 4096 characters"
    }
    assert await WebSearchTool().execute(query="x" * 501) == {
        "error": "query must contain at most 500 characters"
    }
    assert await WeatherTool().execute(location="x" * 201) == {
        "error": "Location must contain at most 200 characters"
    }


@pytest.mark.asyncio
async def test_exchange_rejects_unbounded_or_invalid_url_parameters() -> None:
    tool = ExchangeTool()

    assert await tool.execute(action="rate", base="USDD", target="CNY") == {
        "error": "Currency codes must contain exactly three letters"
    }
    assert await tool.execute(action="convert", amount=float("inf")) == {
        "error": "Amount must be finite and at most 1000000000000"
    }
