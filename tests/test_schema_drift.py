"""Ensure core_schema action enums are always a subset of schema action enums."""
from __future__ import annotations

import pytest

from pc_assistant.tools.application import ApplicationTool
from pc_assistant.tools.clipboard import ClipboardTool
from pc_assistant.tools.exchange import ExchangeTool
from pc_assistant.tools.filesystem import FilesystemTool
from pc_assistant.tools.keyboard import KeyboardTool
from pc_assistant.tools.memory_tool import MemoryTool
from pc_assistant.tools.mouse import MouseTool
from pc_assistant.tools.notification import NotificationTool
from pc_assistant.tools.scheduler import SchedulerTool
from pc_assistant.tools.shell import ShellTool
from pc_assistant.tools.system import SystemTool
from pc_assistant.tools.weather import WeatherTool
from pc_assistant.tools.web import WebTool
from pc_assistant.tools.window import WindowTool


def _extract_action_enums(schema: dict) -> set[str]:
    props = schema.get("parameters", {}).get("properties", {})
    action = props.get("action", {})
    return set(action.get("enum", []))


ALL_TOOLS = [
    ApplicationTool(),
    ClipboardTool(),
    ExchangeTool(),
    FilesystemTool(),
    KeyboardTool(),
    MemoryTool(),
    MouseTool(),
    NotificationTool(),
    SchedulerTool(),
    ShellTool(),
    SystemTool(),
    WeatherTool(),
    WebTool(),
    WindowTool(),
]


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_core_schema_actions_subset_of_schema(tool):
    full_actions = _extract_action_enums(tool.schema())
    core_actions = _extract_action_enums(tool.core_schema())

    if not full_actions and not core_actions:
        return

    extra = core_actions - full_actions
    assert not extra, (
        f"Tool '{tool.name}': core_schema has actions not in schema: {extra}. "
        f"schema={sorted(full_actions)}, core_schema={sorted(core_actions)}"
    )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_core_schema_has_same_name(tool):
    assert tool.schema()["name"] == tool.core_schema()["name"]
