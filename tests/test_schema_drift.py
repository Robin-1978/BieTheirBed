"""Ensure skim_definition action enums are always a subset of schema action enums."""
from __future__ import annotations

import pytest

from knoa_platform.tools.clipboard import ClipboardTool
from knoa_platform.tools.exchange import ExchangeTool
from knoa_platform.tools.hotkey import HotkeyTool
from knoa_platform.tools.memory_tool import MemoryTool
from knoa_platform.tools.mouse import MouseTool
from knoa_platform.tools.notification import NotificationTool
from knoa_platform.tools.press_key import PressKeyTool
from knoa_platform.tools.read_file import ReadFileTool
from knoa_platform.tools.shell import ShellTool
from knoa_platform.tools.type_text import TypeTextTool
from knoa_platform.tools.ui import UiTool
from knoa_platform.tools.weather import WeatherTool
from knoa_platform.tools.web_fetch import WebFetchTool
from knoa_platform.tools.web_search import WebSearchTool
from knoa_platform.tools.window import WindowTool
from knoa_platform.tools.write_file import WriteFileTool
from knoa_platform.tools.registry import ToolRegistry


def _extract_action_enums(schema: dict) -> set[str]:
    props = schema.get("inputSchema", {}).get("properties", {})
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
    UiTool(ui_backend="none"),
    ShellTool(),
    WeatherTool(),
    WebSearchTool(),
    WebFetchTool(),
    WindowTool(),
]


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_skim_definition_actions_subset_of_schema(tool):
    full_actions = _extract_action_enums(tool.definition())
    skim_actions = _extract_action_enums(tool.skim_definition())

    if not full_actions and not skim_actions:
        return

    extra = skim_actions - full_actions
    assert not extra, (
        f"Tool '{tool.name}': skim_definition has actions not in schema: {extra}. "
        f"schema={sorted(full_actions)}, skim_definition={sorted(skim_actions)}"
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_skim_definition_has_same_name(tool):
    assert tool.definition()["name"] == tool.skim_definition()["name"]


def test_generated_tool_help_example_contains_only_required_inputs():
    registry = ToolRegistry()
    registry.register(WindowTool())

    detail = registry.detailed_schema("windows")

    assert detail["examples"] == [{"action": "list"}]
