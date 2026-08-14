from __future__ import annotations

import pytest

from knoa_agent import SemanticSelection, ToolInventory


class SemanticSelector:
    def select(self, query, candidates):
        del candidates
        if "构建日志" in query:
            return SemanticSelection(
                names=frozenset({"mcp__jira__issue_get"}),
                mode="bge",
            )
        return SemanticSelection()


class Client:
    def __init__(self) -> None:
        self.calls = 0

    async def list_tools(self):
        self.calls += 1
        return (
            {
                "name": "web_search",
                "description": "Search the web for relevant sources",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "name": "write_file",
                "description": "Write content to a local file",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "mcp__jira__issue_get",
                "description": "Get one Jira issue",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": "Jira issue key",
                            "pattern": "^[A-Z]+-[0-9]+$",
                        }
                    },
                    "required": ["issue_key"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "tool_help",
                "description": "Describe an available tool",
                "inputSchema": {"type": "object", "properties": {}},
            },
        )


class ComplexBuiltinClient:
    async def list_tools(self):
        return (
            {
                "name": "task",
                "description": "Manage tasks and executions",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "get", "update", "rerun"],
                        },
                        "task_id": {"type": "string"},
                        "execution_id": {"type": "string"},
                        "title": {"type": "string"},
                        "goal": {"type": "string"},
                        "include_archived": {"type": "boolean"},
                        "expected_revision": {"type": "integer"},
                        "launch": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["immediate", "cron"],
                                },
                                "cron": {"type": "string"},
                            },
                            "required": ["kind"],
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "create_task",
                "description": "Create a task",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "goal": {"type": "string"},
                        "launch": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["immediate", "cron"],
                                },
                                "cron": {"type": "string"},
                                "timezone": {"type": "string"},
                            },
                            "required": ["kind"],
                        },
                    },
                    "required": ["title", "goal", "launch"],
                },
            },
        )


@pytest.mark.asyncio
async def test_inventory_cache_is_keyed_by_session_and_scope_digest() -> None:
    client = Client()
    inventory = ToolInventory()

    first = await inventory.load("session-a", "digest-a", client)
    again = await inventory.load("session-a", "digest-a", client)
    changed = await inventory.load("session-a", "digest-b", client)

    assert first is again
    assert changed is not first
    assert client.calls == 2
    assert [tool["name"] for tool in first.tools] == [
        "mcp__jira__issue_get",
        "tool_help",
        "web_search",
        "write_file",
    ]


@pytest.mark.asyncio
async def test_projection_keeps_builtin_tools_static_and_mcp_tools_deferred() -> None:
    inventory = ToolInventory(schema_char_budget=2000)
    snapshot = await inventory.load("session-a", "digest-a", Client())

    selected = inventory.project("session-a", snapshot)

    assert [tool["name"] for tool in selected] == [
        "tool_help",
        "web_search",
        "write_file",
    ]
    assert selected[1]["inputSchema"]["required"] == ["query"]
    assert selected[1]["description"] == "Search the web for relevant sources"

    assert inventory.activate(
        "session-a",
        snapshot,
        {"mcp__jira__issue_get"},
    ) == ("mcp__jira__issue_get",)
    selected = inventory.project("session-a", snapshot)

    jira = selected[0]
    assert jira["name"] == "mcp__jira__issue_get"
    assert jira["description"] == "Get one Jira issue"
    assert jira["inputSchema"] == {
        "type": "object",
        "properties": {"issue_key": {"type": "string"}},
        "required": ["issue_key"],
    }


@pytest.mark.asyncio
async def test_resource_task_automatically_injects_tools_from_its_mcp_source() -> None:
    inventory = ToolInventory(schema_char_budget=4000, semantic_selector=SemanticSelector())
    snapshot = await inventory.load("session-a", "digest-a", Client())

    projection = await inventory.project_for_turn(
        "session-a",
        snapshot,
        "MCP server: jira\nMCP resource: jira://assigned/event-1",
    )

    assert projection.mode == "source"
    assert projection.matched_names == ("mcp__jira__issue_get",)
    assert projection.schema_hits == 1
    assert "mcp__jira__issue_get" in {tool["name"] for tool in projection.tools}


@pytest.mark.asyncio
async def test_optional_bge_match_is_or_merged_with_deterministic_selection() -> None:
    inventory = ToolInventory(schema_char_budget=4000, semantic_selector=SemanticSelector())
    snapshot = await inventory.load("session-a", "digest-a", Client())

    projection = await inventory.project_for_turn(
        "session-a",
        snapshot,
        "分析构建日志为什么失败",
    )

    assert projection.mode == "bge"
    assert projection.matched_names == ("mcp__jira__issue_get",)
    assert projection.schema_hits == 1


@pytest.mark.asyncio
async def test_selector_fails_closed_when_static_schemas_exceed_budget() -> None:
    inventory = ToolInventory(schema_char_budget=1000)
    snapshot = await inventory.load("session-a", "digest-a", Client())
    oversized_tool = {
        "name": "oversized_builtin",
        "description": "",
        "inputSchema": {
            "type": "object",
            "properties": {
                f"argument_{index:04d}": {"type": "string"}
                for index in range(100)
            },
        },
    }
    oversized = type(snapshot)(tools=(oversized_tool,), schema_chars=10_000)

    with pytest.raises(ValueError, match="exceed"):
        inventory.project("session-a", oversized)


@pytest.mark.asyncio
async def test_complex_task_parameters_stay_behind_tool_help() -> None:
    inventory = ToolInventory(schema_char_budget=2000)
    snapshot = await inventory.load(
        "session-a",
        "digest-a",
        ComplexBuiltinClient(),
    )

    projected = {tool["name"]: tool for tool in inventory.project("session-a", snapshot)}

    assert set(projected["task"]["inputSchema"]["properties"]) == {
        "action",
        "task_id",
        "execution_id",
    }
    assert set(
        projected["create_task"]["inputSchema"]["properties"]["launch"]["properties"]
    ) == {"kind"}
