from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
)
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.capabilities import (
    CapabilityGateway,
    CapabilityMCPHost,
    GatewayMCPClient,
)
from knoa_platform.tasks.repository import TaskRepository
from knoa_platform.tasks.tool_commit import DurableToolCommitService
from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from knoa_platform.tools.registry import ToolRegistry


class EchoTool(ToolBase):
    name = "echo"
    description = "Echo a message."
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.NETWORK})
    risk = ToolRisk.LOW

    async def execute(self, **kwargs):
        return {"echo": kwargs["message"]}

    def definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "pattern": "^.{1,64}$",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        }


class InternalWriteTool(EchoTool):
    name = "internal_write"
    effect = ToolEffect.INTERNAL_WRITE


@pytest.mark.asyncio
async def test_gateway_can_issue_a_toolless_system_agent_grant(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="review-a"),
        run_id="review-a",
        client_request_id="review-a",
        capabilities=frozenset({ToolCapability.NETWORK}),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
        allow_tools=False,
    )

    async with GatewayMCPClient(gateway).bind(grant) as client:
        assert await client.list_tools() == ()


@pytest.mark.asyncio
async def test_gateway_projects_builtin_handler_as_standard_mcp_tool(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        run_id="turn-a",
        client_request_id="request-a",
        capabilities=frozenset({ToolCapability.NETWORK}),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
    )
    client = GatewayMCPClient(gateway)
    try:
        async with client.bind(grant) as bound:
            definitions = await bound.list_tools()
            result = await bound.call_tool(
                ProposedToolCall(
                    call_id="model-call-a",
                    name="echo",
                    arguments={"message": "hello"},
                )
            )
    finally:
        await gateway.grants.revoke(grant.token)

    assert definitions == (
        {
            "name": "echo",
            "description": "Echo a message.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "pattern": "^.{1,64}$",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    )
    assert result.call_id == "model-call-a"
    assert result.status == "completed"
    assert result.output == {"echo": "hello"}


@pytest.mark.asyncio
async def test_gateway_enforces_invocation_tool_call_budget(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        run_id="turn-budget",
        client_request_id="request-budget",
        capabilities=frozenset({ToolCapability.NETWORK}),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
        max_tool_calls=1,
    )

    async with GatewayMCPClient(gateway).bind(grant) as bound:
        await bound.list_tools()
        first = await bound.call_tool(
            ProposedToolCall(
                call_id="call-one",
                name="echo",
                arguments={"message": "first"},
            )
        )
        with pytest.raises(MCPError, match="budget exhausted"):
            await bound.call_tool(
                ProposedToolCall(
                    call_id="call-two",
                    name="echo",
                    arguments={"message": "second"},
                )
            )

    assert first.status == "completed"


@pytest.mark.asyncio
async def test_gateway_commits_internal_write_tool_for_durable_task(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(InternalWriteTool())
    database = tmp_path / "assistant.db"
    scope = RuntimeSessionRepository(
        database,
        handle_factory=lambda: "session-a",
    ).create("principal-a")
    repository = TaskRepository(database)
    task, _created = repository.create(
        scope,
        client_request_id="request-a",
        goal="exercise the capability gateway",
    )
    repository.claim_next("worker-a", lease_seconds=30)
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=scope,
        run_id=task.task_id,
        client_request_id="request-a",
        capabilities=frozenset({ToolCapability.NETWORK}),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=DurableToolCommitService(repository),
        tool_names=frozenset({"internal_write"}),
    )
    client = GatewayMCPClient(gateway)
    try:
        async with client.bind(grant) as bound:
            await bound.list_tools()
            result = await bound.call_tool(
                ProposedToolCall(
                    call_id="model-call-a",
                    name="internal_write",
                    arguments={"message": "hello"},
                )
            )
    finally:
        await gateway.grants.revoke(grant.token)

    assert result.status == "completed"
    assert result.output == {"echo": "hello"}


@pytest.mark.asyncio
async def test_gateway_filters_inventory_and_calls_by_grant(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        run_id="turn-a",
        client_request_id="request-a",
        capabilities=frozenset(),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
    )
    client = GatewayMCPClient(gateway)
    async with client.bind(grant) as bound:
        assert await bound.list_tools() == ()
        result = await bound.call_tool(
            ProposedToolCall(
                call_id="model-call-a",
                name="echo",
                arguments={"message": "hello"},
            )
        )

    assert result.status == "rejected"
    assert result.code == "capability_denied"


@pytest.mark.asyncio
async def test_gateway_rejects_revoked_grant(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        run_id="turn-a",
        client_request_id="request-a",
        capabilities=frozenset({ToolCapability.NETWORK}),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
    )
    client = GatewayMCPClient(gateway)
    async with client.bind(grant) as bound:
        await gateway.grants.revoke(grant.token)
        with pytest.raises(Exception):
            await bound.list_tools()


@pytest.mark.asyncio
async def test_gateway_exposes_only_granted_artifacts_as_standard_resources(
    tmp_path,
) -> None:
    registry = ToolRegistry()
    artifacts = ArtifactStore(
        tmp_path / "attachments",
        db_path=tmp_path / "assistant.db",
    )
    first = artifacts.put_data_url(
        "session-a",
        "data:text/plain;base64," + base64.b64encode(b"hello").decode(),
        name="first.txt",
    )
    second = artifacts.put_data_url(
        "session-a",
        "data:text/plain;base64," + base64.b64encode(b"secret").decode(),
        name="second.txt",
    )
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
        artifacts,
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        run_id="turn-a",
        client_request_id="request-a",
        capabilities=frozenset(),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
        artifact_ids=frozenset({first["artifact_id"]}),
    )

    client = GatewayMCPClient(gateway)
    async with client.bind(grant) as bound:
        resources = await bound.list_resources()
        contents = await bound.read_resource(resources[0]["uri"])
        with pytest.raises(Exception):
            await bound.read_resource(
                CapabilityGateway.artifact_uri(second["artifact_id"])
            )

    assert [item["name"] for item in resources] == ["first.txt"]
    assert contents[0]["text"] == "hello"


@pytest.mark.asyncio
async def test_gateway_exposes_grant_as_authenticated_standard_http_mcp(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    gateway = CapabilityGateway(
        registry,
        ToolStep(registry, ToolArgumentPolicy(tmp_path)),
    )
    grant = await gateway.grants.issue(
        scope=RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        run_id="turn-a",
        client_request_id="request-a",
        capabilities=frozenset({ToolCapability.NETWORK}),
        cancellation=asyncio.Event(),
        confirmation=None,
        tool_commit=None,
        tool_names=frozenset({"echo"}),
    )
    host = CapabilityMCPHost(gateway, port=0)
    await host.start()
    try:
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {grant.token}"}
        )
        async with streamable_http_client(
            host.endpoint,
            http_client=http_client,
        ) as streams, ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            result = await session.list_tools()
        assert [tool.name for tool in result.tools] == ["echo"]
    finally:
        await host.stop()
        await http_client.aclose()
