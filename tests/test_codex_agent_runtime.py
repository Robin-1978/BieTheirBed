from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_agent_contracts import (
    CreateRuntimeSession,
    McpEndpointGrant,
    RuntimeInterruptCommand,
    RuntimeInteractionResolution,
    RuntimeSteerCommand,
    RuntimeTurnRequest,
    TextPart,
)
from knoa_codex_agent import CodexAgentRuntime, CodexSessionRepository
from knoa_codex_agent.app_server import CodexAppServerClient


class FakeAppServer:
    def __init__(self, env) -> None:
        self.env = dict(env)
        self.requests = []
        self.responses = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.mcp_servers = [{"name": "knoa_platform"}]

    async def start(self) -> None:
        return None

    async def request(self, method, params):
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-a"}}
        if method in {"thread/read", "thread/resume"}:
            return {"thread": {"id": params["threadId"], "turns": []}}
        if method == "mcpServerStatus/list":
            return {"data": self.mcp_servers}
        if method == "mcpServer/resource/read":
            return {"contents": [{"uri": params["uri"], "text": "artifact"}]}
        if method == "turn/start":
            return {"turn": {"id": "turn-a", "items": [], "status": "inProgress"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        return {}

    async def respond(self, request_id, *, result=None, error=None):
        self.responses.append((request_id, result, error))

    async def close(self) -> None:
        self.closed = True

    async def emit(self, message) -> None:
        await self.queue.put(message)

    async def events(self):
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item


class ClientFactory:
    def __init__(self) -> None:
        self.clients = []

    def __call__(self, env):
        client = FakeAppServer(env)
        self.clients.append(client)
        return client


def grant() -> McpEndpointGrant:
    return McpEndpointGrant(
        server_id="knoa-platform-capabilities",
        transport="streamable_http",
        endpoint="http://127.0.0.1:9530/mcp",
        authorization="grant-token",
        expires_at=9999999999.0,
        scope_digest="a" * 64,
        binding_epoch=1,
    )


def test_codex_runtime_reuses_normal_codex_home_when_home_is_not_configured(
    tmp_path: Path,
) -> None:
    runtime = CodexAgentRuntime(
        CodexSessionRepository(tmp_path / "sessions.db"),
        cwd=tmp_path,
    )

    client = runtime._new_stdio_client({"KNOA_CAPABILITY_GRANT": "grant-token"})

    assert isinstance(client, CodexAppServerClient)
    assert client._env == {"KNOA_CAPABILITY_GRANT": "grant-token"}


def session_repository(tmp_path: Path) -> CodexSessionRepository:
    return CodexSessionRepository(
        tmp_path / "sessions.db",
        handle_factory=lambda: "runtime-session-a",
    )


@pytest.mark.asyncio
async def test_codex_runtime_maps_thread_turn_and_stream_events(tmp_path: Path) -> None:
    factory = ClientFactory()
    runtime = CodexAgentRuntime(
        session_repository(tmp_path),
        home=tmp_path / "codex-home",
        cwd=tmp_path,
        client_factory=factory,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="bind-a", binding_epoch=1)
    )
    assert session.runtime_session_ref == "runtime-session-a"

    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="hello"),),
            mcp=grant(),
        )
    )
    client = factory.clients[-1]
    await client.emit(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-a",
                "turnId": "turn-a",
                "delta": "done",
            },
        }
    )
    await client.emit(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-a",
                "turn": {"id": "turn-a", "status": "completed", "items": []},
            },
        }
    )

    events = [event async for event in turn.events]

    assert [event.event_type for event in events] == [
        "assistant_delta",
        "turn_finished",
    ]
    assert events[-1].final_output == "done"
    assert client.env == {"KNOA_CAPABILITY_GRANT": "grant-token"}
    started = next(params for method, params in client.requests if method == "thread/start")
    assert started["config"]["mcp_servers"]["knoa_platform"]["required"] is True
    assert started["config"]["apps"]["_default"]["enabled"] is False
    record = runtime._sessions.get(session.runtime_session_ref)
    assert record.upstream_thread_ref == "thread-a"


@pytest.mark.asyncio
async def test_codex_runtime_steers_interrupts_and_resolves_approval(tmp_path: Path) -> None:
    factory = ClientFactory()
    runtime = CodexAgentRuntime(
        CodexSessionRepository(tmp_path / "sessions.db"),
        home=tmp_path / "codex-home",
        cwd=tmp_path,
        client_factory=factory,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="bind-a", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="hello"),),
            mcp=grant(),
        )
    )
    client = factory.clients[-1]
    consume = asyncio.create_task(anext(turn.events))
    await client.emit(
        {
            "id": 77,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-a", "turnId": "turn-a"},
        }
    )
    interaction = await consume
    assert interaction.event_type == "interaction_requested"
    resolved = await runtime.resolve_interaction(
        RuntimeInteractionResolution(
            session=session,
            runtime_turn_ref="turn-a",
            interaction_id=interaction.interaction_id,
            interaction_epoch=interaction.interaction_epoch,
            command_id="resolve-a",
            value={"decision": "decline"},
        )
    )

    steer = await runtime.steer_turn(
        RuntimeSteerCommand(
            session=session,
            runtime_turn_ref="turn-a",
            command_id="steer-a",
            input=(TextPart(text="continue"),),
        )
    )
    interrupt = await runtime.interrupt_turn(
        RuntimeInterruptCommand(
            session=session,
            runtime_turn_ref="turn-a",
            command_id="interrupt-a",
        )
    )

    assert steer.status == "accepted"
    assert interrupt.status == "accepted"
    assert resolved.status == "accepted"
    assert client.responses == [(77, {"decision": "decline"}, None)]


@pytest.mark.asyncio
async def test_codex_runtime_rejects_session_wide_approval(tmp_path: Path) -> None:
    factory = ClientFactory()
    runtime = CodexAgentRuntime(
        CodexSessionRepository(tmp_path / "sessions.db"),
        home=tmp_path / "codex-home",
        cwd=tmp_path,
        client_factory=factory,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="bind-a", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="hello"),),
            mcp=grant(),
        )
    )
    client = factory.clients[-1]
    consume = asyncio.create_task(anext(turn.events))
    await client.emit(
        {
            "id": 88,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-a", "turnId": "turn-a"},
        }
    )
    interaction = await consume

    result = await runtime.resolve_interaction(
        RuntimeInteractionResolution(
            session=session,
            runtime_turn_ref="turn-a",
            interaction_id=interaction.interaction_id,
            interaction_epoch=interaction.interaction_epoch,
            command_id="resolve-a",
            value={"decision": "acceptForSession"},
        )
    )

    assert result.status == "rejected"
    assert client.responses == []


@pytest.mark.asyncio
async def test_codex_runtime_exposes_user_input_as_generic_schema(tmp_path: Path) -> None:
    factory = ClientFactory()
    runtime = CodexAgentRuntime(
        CodexSessionRepository(tmp_path / "sessions.db"),
        home=tmp_path / "codex-home",
        cwd=tmp_path,
        client_factory=factory,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="bind-a", binding_epoch=1)
    )
    turn = await runtime.start_turn(
        RuntimeTurnRequest(
            session=session,
            operation_id="operation-a",
            input=(TextPart(text="hello"),),
            mcp=grant(),
        )
    )
    client = factory.clients[-1]
    consume = asyncio.create_task(anext(turn.events))
    await client.emit(
        {
            "id": 99,
            "method": "item/tool/requestUserInput",
            "params": {
                "threadId": "thread-a",
                "turnId": "turn-a",
                "itemId": "item-a",
                "questions": [
                    {
                        "id": "target",
                        "header": "Target",
                        "question": "Choose a target",
                        "isOther": False,
                        "isSecret": False,
                        "options": [
                            {"label": "alpha", "description": "First"},
                            {"label": "beta", "description": "Second"},
                        ],
                    }
                ],
                "isBlocking": True,
                "autoResolutionMs": None,
            },
        }
    )
    interaction = await consume

    assert interaction.kind == "user_input"
    assert interaction.resolution_schema == {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "title": "Target",
                "minLength": 1,
                "maxLength": 4000,
                "enum": ["alpha", "beta"],
            }
        },
        "required": ["target"],
        "additionalProperties": False,
    }
    assert interaction.display["fields"][0]["options"][0]["value"] == "alpha"

    result = await runtime.resolve_interaction(
        RuntimeInteractionResolution(
            session=session,
            runtime_turn_ref="turn-a",
            interaction_id=interaction.interaction_id,
            interaction_epoch=interaction.interaction_epoch,
            command_id="resolve-a",
            value={"target": "beta"},
        )
    )

    assert result.status == "accepted"
    assert client.responses == [
        (99, {"answers": {"target": {"answers": ["beta"]}}}, None)
    ]


@pytest.mark.asyncio
async def test_codex_runtime_rejects_extra_mcp_server_inventory(tmp_path: Path) -> None:
    factory = ClientFactory()
    runtime = CodexAgentRuntime(
        CodexSessionRepository(tmp_path / "sessions.db"),
        home=tmp_path / "codex-home",
        cwd=tmp_path,
        client_factory=factory,
    )
    session = await runtime.create_session(
        CreateRuntimeSession(operation_id="bind-a", binding_epoch=1)
    )
    next_client = FakeAppServer({})
    next_client.mcp_servers = [
        {"name": "knoa_platform"},
        {"name": "untrusted_external_server"},
    ]
    runtime._client_factory = lambda _env: next_client
    with pytest.raises(RuntimeError, match="only the Knoa capability server"):
        await runtime.start_turn(
            RuntimeTurnRequest(
                session=session,
                operation_id="operation-a",
                input=(TextPart(text="hello"),),
                mcp=grant(),
            )
        )
