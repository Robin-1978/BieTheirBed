from __future__ import annotations

import pytest
from pc_assistant.tools.base import ToolBase
from pc_assistant.tools.registry import ToolRegistry


class DummyTool(ToolBase):
    name = "dummy"
    description = "A dummy tool for testing"

    async def execute(self, **kwargs):
        return {"result": kwargs.get("input", "none")}

    def schema(self):
        return {"name": self.name, "description": self.description}


def test_tool_base_is_abstract():
    with pytest.raises(TypeError):
        ToolBase()


def test_dummy_tool():
    tool = DummyTool()
    assert tool.name == "dummy"
    assert tool.description == "A dummy tool for testing"
    assert repr(tool) == "<Tool dummy>"


def test_registry_register():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    assert "dummy" in registry
    assert len(registry) == 1


def test_registry_get():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    assert registry.get("dummy") is tool
    assert registry.get("nonexistent") is None


def test_registry_list_tools():
    registry = ToolRegistry()
    registry.register(DummyTool())
    assert registry.list_tools() == ["dummy"]


def test_registry_exposes_schema_and_normalizes_calls():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    assert registry.normalize_call("dummy", {"value": 1}) == ("dummy", {"value": 1})


def test_registry_empty_name():
    class NoName(ToolBase):
        description = "no name"

        async def execute(self, **kwargs):
            return {}

        def schema(self):
            return {}

    tool = NoName()
    tool.name = ""
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(tool)


@pytest.mark.asyncio
async def test_registry_internal_commit():
    registry = ToolRegistry()
    registry.register(DummyTool())
    result = await registry._commit("dummy", input="test")
    assert result == {"result": "test"}


@pytest.mark.asyncio
async def test_registry_internal_commit_missing():
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        await registry._commit("nonexistent")
