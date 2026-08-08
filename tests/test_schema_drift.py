"""Ensure skim_schema action enums are always a subset of schema action enums."""
from __future__ import annotations

import pytest

from pc_assistant.tools.clipboard import ClipboardTool
from pc_assistant.tools.exchange import ExchangeTool
from pc_assistant.tools.hotkey import HotkeyTool
from pc_assistant.tools.memory_tool import MemoryTool
from pc_assistant.tools.mouse import MouseTool
from pc_assistant.tools.notification import NotificationTool
from pc_assistant.tools.press_key import PressKeyTool
from pc_assistant.tools.read_file import ReadFileTool
from pc_assistant.tools.shell import ShellTool
from pc_assistant.tools.type_text import TypeTextTool
from pc_assistant.tools.weather import WeatherTool
from pc_assistant.tools.web_fetch import WebFetchTool
from pc_assistant.tools.web_search import WebSearchTool
from pc_assistant.tools.window import WindowTool
from pc_assistant.tools.write_file import WriteFileTool
from pc_assistant.tools.registry import ToolRegistry


def _extract_action_enums(schema: dict) -> set[str]:
    props = schema.get("parameters", {}).get("properties", {})
    action = props.get("action", {})
    return set(action.get("enum", []))


ALL_TOOLS = [
    ClipboardTool(),
    ExchangeTool(),
    ReadFileTool(),
    WriteFileTool(),
    PressKeyTool(),
    TypeTextTool(),
    HotkeyTool(),
    MemoryTool(),
    MouseTool(),
    NotificationTool(),
    ShellTool(),
    WeatherTool(),
    WebSearchTool(),
    WebFetchTool(),
    WindowTool(),
]


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_skim_schema_actions_subset_of_schema(tool):
    full_actions = _extract_action_enums(tool.schema())
    skim_actions = _extract_action_enums(tool.skim_schema())

    if not full_actions and not skim_actions:
        return

    extra = skim_actions - full_actions
    assert not extra, (
        f"Tool '{tool.name}': skim_schema has actions not in schema: {extra}. "
        f"schema={sorted(full_actions)}, skim_schema={sorted(skim_actions)}"
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_skim_schema_has_same_name(tool):
    assert tool.schema()["name"] == tool.skim_schema()["name"]


def test_generated_tool_help_example_contains_only_required_inputs():
    registry = ToolRegistry()
    registry.register(WindowTool())

    detail = registry.detailed_schema("windows")

    assert detail["examples"] == [{"action": "list"}]
