"""Tool recall tags: seed format, lookup, and document enrichment."""

from __future__ import annotations

import json

from knoa_agent import tool_selector
from knoa_agent.tool_selector import (
    MAX_TAGS_PER_TOOL,
    BgeToolSelector,
    tool_tags_for,
)


def _reset_cache() -> None:
    tool_selector._TOOL_TAGS = None


def test_tags_file_has_scored_pool_for_all_mcp_tools() -> None:
    _reset_cache()
    try:
        raw = json.loads(tool_selector._TAGS_PATH.read_text(encoding="utf-8"))
    finally:
        _reset_cache()
    assert len(raw) == 28
    for name, pool in raw.items():
        assert name.startswith("mcp__"), name
        assert isinstance(pool, dict) and pool, name
        assert len(pool) <= MAX_TAGS_PER_TOOL, name
        assert all(score > 0 for score in pool.values()), name


def test_tool_tags_for_returns_top_ranked_unknown_is_empty() -> None:
    _reset_cache()
    try:
        tags = tool_tags_for("mcp__browser__click")
        assert "点击" in tags
        assert len(tags) <= MAX_TAGS_PER_TOOL
        assert tool_tags_for("mcp__nope__missing") == ()
        assert tool_tags_for("read_file") == ()
    finally:
        _reset_cache()


def test_document_appends_tags_after_description() -> None:
    doc = BgeToolSelector._document(
        "mcp__browser__click", "Click one clickable page element."
    )
    assert doc.startswith("browser click. Click one clickable page element.")
    assert "点击" in doc
    plain = BgeToolSelector._document("read_file", "Read a file.")
    assert plain == "read file. Read a file."
