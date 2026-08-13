from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import (
    ProposedToolCall,
    ToolArgumentPolicy,
    ToolStep,
    ToolStepContext,
)
from knoa_platform.config import AppConfig
from knoa_platform.extensions import ExtensionManager, ExtensionState
from knoa_platform.extensions.mcp import (
    MCPResourceCapabilities,
    MCPResourceDefinition,
    MCPResourceSnapshot,
    MCPServerProvider,
    MCPToolDefinition,
    StdioMCPClient,
    StreamableHTTPMCPClient,
    _negotiate_session,
)
from knoa_platform.extensions.models import MCPServerConfig
from knoa_platform.tools.base import ToolCapability, ToolOriginKind
from knoa_platform.tools.registry import ToolRegistry


class _FakeMCPClient:
    def __init__(
        self,
        definitions: tuple[MCPToolDefinition, ...],
        result=None,
    ) -> None:
        self.definitions = definitions
        self.result = result or SimpleNamespace(
            content=[SimpleNamespace(type="text", text="pong")],
            structuredContent=None,
            isError=False,
        )
        self.calls: list[tuple[str, dict]] = []
        self.started = False
        self.closed = False
        self.notification_handler = None

    def set_notification_handler(self, handler) -> None:
        self.notification_handler = handler

    async def start(self) -> None:
        self.started = True

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
        return self.definitions

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self.result

    def resource_capabilities(self) -> MCPResourceCapabilities:
        return MCPResourceCapabilities()

    async def list_resources(self) -> tuple[MCPResourceDefinition, ...]:
        return ()

    async def read_resource(self, uri: str) -> MCPResourceSnapshot:
        del uri
        return MCPResourceSnapshot(contents=())

    async def subscribe_resource(self, uri: str) -> None:
        del uri

    async def unsubscribe_resource(self, uri: str) -> None:
        del uri

    async def close(self) -> None:
        self.closed = True


class _Confirmation:
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.calls = 0

    async def confirm(self, scope, run_id, call, reason: str) -> bool:
        del scope, run_id, call, reason
        self.calls += 1
        return self.approved


def _context(
    capabilities: frozenset[ToolCapability],
    confirmation: _Confirmation | None = None,
) -> ToolStepContext:
    return ToolStepContext(
        scope=RuntimeScope(principal_id="local", session_handle="session-a"),
        run_id="run-a",
        client_request_id="request-a",
        capabilities=capabilities,
        cancellation=asyncio.Event(),
        confirmation=confirmation,
    )


def _config(
    *,
    effect: str = "read_only",
    risk: str = "low",
    capabilities: list[str] | None = None,
) -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {
            "enabled": True,
            "url": "https://mcp.example.test/mcp",
            "tools": {
                "ping": {
                    "effect": effect,
                    "capabilities": capabilities or ["network"],
                    "risk": risk,
                }
            },
        }
    )


def _provider(config: MCPServerConfig, client: _FakeMCPClient) -> MCPServerProvider:
    return MCPServerProvider(
        "docs",
        config,
        client_factory=lambda _config: client,
    )


@pytest.mark.asyncio
async def test_mcp_negotiation_prefers_discover_and_falls_back_only_for_legacy() -> None:
    from mcp import types
    from mcp.shared.exceptions import MCPError

    modern_result = SimpleNamespace(capabilities=SimpleNamespace(resources=None))

    class _ModernSession:
        async def discover(self):
            return modern_result

        async def initialize(self):
            raise AssertionError("legacy handshake must not run")

    assert await _negotiate_session(_ModernSession(), 1) == (modern_result, True)

    legacy_result = SimpleNamespace(capabilities=SimpleNamespace(resources=None))

    class _LegacySession:
        async def discover(self):
            raise MCPError(types.METHOD_NOT_FOUND, "not found")

        async def initialize(self):
            return legacy_result

    assert await _negotiate_session(_LegacySession(), 1) == (legacy_result, False)

    class _BrokenModernSession:
        async def discover(self):
            raise MCPError(types.INTERNAL_ERROR, "broken")

        async def initialize(self):
            raise AssertionError("non-legacy discover failures must not downgrade")

    with pytest.raises(MCPError):
        await _negotiate_session(_BrokenModernSession(), 1)


@pytest.mark.asyncio
async def test_mcp_discovery_registers_only_locally_configured_tools(
    tmp_path: Path,
) -> None:
    client = _FakeMCPClient(
        (
            MCPToolDefinition(
                name="ping",
                description="Ping the service",
                input_schema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
            ),
            MCPToolDefinition(
                name="unconfigured",
                description="Must remain hidden",
                input_schema={"type": "object", "properties": {}},
            ),
        )
    )
    registry = ToolRegistry()
    manager = ExtensionManager(registry, (_provider(_config(), client),))
    await manager.start()

    assert registry.list_tools() == ["mcp__docs__ping"]
    origin = registry.origin("mcp__docs__ping")
    assert origin is not None
    assert origin.kind is ToolOriginKind.MCP
    assert manager.statuses[0].state is ExtensionState.RUNNING

    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    call = ProposedToolCall(
        call_id="call-a",
        name="mcp__docs__ping",
        arguments={"message": "hello"},
    )
    denied = await step.execute(
        _context(frozenset({ToolCapability.NETWORK})),
        call,
    )
    completed = await step.execute(
        _context(frozenset({ToolCapability.NETWORK, ToolCapability.MCP})),
        call,
    )

    assert denied.code == "capability_denied"
    assert completed.status == "completed"
    assert completed.output == {"content": [{"type": "text", "text": "pong"}]}
    assert client.calls == [("ping", {"message": "hello"})]

    await manager.stop()
    assert client.closed is True
    assert registry.list_tools() == []


@pytest.mark.asyncio
async def test_dotted_remote_tool_name_is_normalized_for_core() -> None:
    config = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "url": "https://mcp.example.test/mcp",
            "tools": {
                "monitor.list_observations": {
                    "effect": "read_only",
                    "risk": "low",
                }
            },
        }
    )
    client = _FakeMCPClient(
        (
            MCPToolDefinition(
                name="monitor.list_observations",
                description="List observations",
                input_schema={"type": "object", "properties": {}},
            ),
        )
    )
    registry = ToolRegistry()
    manager = ExtensionManager(
        registry,
        (
            MCPServerProvider(
                "monitor",
                config,
                client_factory=lambda _config: client,
            ),
        ),
    )

    await manager.start()

    assert registry.list_tools() == ["mcp__monitor__monitor_list_observations"]
    policy = registry.policy("mcp__monitor__monitor_list_observations")
    assert policy is not None
    assert policy.capabilities == frozenset({ToolCapability.MCP})
    await manager.stop()


@pytest.mark.asyncio
async def test_mcp_side_effect_uses_standard_confirmation_boundary(
    tmp_path: Path,
) -> None:
    client = _FakeMCPClient(
        (
            MCPToolDefinition(
                name="ping",
                description="Publish a ping",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        )
    )
    registry = ToolRegistry()
    manager = ExtensionManager(
        registry,
        (_provider(_config(effect="external_side_effect", risk="high"), client),),
    )
    await manager.start()
    step = ToolStep(registry, ToolArgumentPolicy(tmp_path))
    call = ProposedToolCall(call_id="call-a", name="mcp__docs__ping")

    missing = await step.execute(
        _context(frozenset({ToolCapability.NETWORK, ToolCapability.MCP})),
        call,
    )
    confirmation = _Confirmation(True)
    completed = await step.execute(
        _context(
            frozenset({ToolCapability.NETWORK, ToolCapability.MCP}),
            confirmation,
        ),
        call,
    )

    assert missing.code == "confirmation_required"
    assert completed.status == "completed"
    assert confirmation.calls == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_mcp_error_and_large_results_fail_as_tool_results(tmp_path: Path) -> None:
    definition = MCPToolDefinition(
        name="ping",
        description="Ping",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    error_client = _FakeMCPClient(
        (definition,),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="upstream failed")],
            structuredContent=None,
            isError=True,
        ),
    )
    large_client = _FakeMCPClient(
        (definition,),
        SimpleNamespace(
            content=[],
            structuredContent={"payload": "x" * 600_000},
            isError=False,
        ),
    )

    async def execute(client: _FakeMCPClient):
        registry = ToolRegistry()
        manager = ExtensionManager(registry, (_provider(_config(), client),))
        await manager.start()
        result = await ToolStep(registry, ToolArgumentPolicy(tmp_path)).execute(
            _context(frozenset({ToolCapability.NETWORK, ToolCapability.MCP})),
            ProposedToolCall(call_id="call-a", name="mcp__docs__ping"),
        )
        await manager.stop()
        return result

    error = await execute(error_client)
    large = await execute(large_client)

    assert error.status == "failed"
    assert error.message == "upstream failed"
    assert large.status == "failed"
    assert "size limit" in large.message


def test_mcp_config_rejects_unsafe_urls_names_and_unknown_policy() -> None:
    with pytest.raises(ValidationError, match="must not contain credentials"):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://user:secret@example.test/mcp",
            }
        )
    dotted = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "url": "https://example.test/mcp",
            "tools": {
                "safe.tool": {
                    "effect": "read_only",
                    "risk": "low",
                }
            },
        }
    )
    assert "safe.tool" in dotted.tools
    with pytest.raises(ValidationError, match="safe characters"):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://example.test/mcp",
                "tools": {
                    "unsafe/tool": {
                        "effect": "read_only",
                        "risk": "low",
                    }
                },
            }
        )
    with pytest.raises(ValidationError, match="unsafe path"):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://example.test/mcp",
                "resource_tasks": {
                    "assigned": {
                        "uri": "jira://assigned-to-me/events/%2Fsecret",
                        "principal_id": "personal:owner",
                        "session_handle": "session-a",
                    }
                },
            }
        )
    with pytest.raises(ValidationError):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://example.test/mcp",
                "tools": {
                    "ping": {
                        "effect": "unknown",
                        "risk": "low",
                    }
                },
            }
        )
    with pytest.raises(ValidationError, match="server IDs"):
        AppConfig(
            mcp_servers={
                "unsafe.server": {
                    "enabled": True,
                    "url": "https://example.test/mcp",
                }
            }
        )


def test_mcp_config_enforces_transport_specific_fields() -> None:
    stdio = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "monitor", "mcp"],
            "working_directory": ".",
            "inherit_env": ["MONITOR_DB_PATH"],
        }
    )
    assert stdio.command == "python"
    with pytest.raises(ValidationError, match="requires a command"):
        MCPServerConfig.model_validate({"enabled": True, "transport": "stdio"})
    with pytest.raises(ValidationError, match="must not configure a URL"):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "transport": "stdio",
                "command": "python",
                "url": "https://example.test/mcp",
            }
        )
    with pytest.raises(ValidationError, match="must not configure stdio fields"):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://example.test/mcp",
                "command": "python",
            }
        )


def test_mcp_resource_task_config_is_explicit_and_strict() -> None:
    config = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "url": "https://example.test/mcp",
            "resource_tasks": {
                "assigned": {
                    "uri": "jira://assigned-to-me",
                    "principal_id": "personal:owner",
                    "session_handle": "session-a",
                    "priority": 4,
                }
            },
        }
    )
    assert config.resource_tasks["assigned"].uri == "jira://assigned-to-me"
    with pytest.raises(ValidationError, match="must not contain credentials"):
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "url": "https://example.test/mcp",
                "resource_tasks": {
                    "assigned": {
                        "uri": "jira://user:secret@assigned-to-me",
                        "principal_id": "personal:owner",
                        "session_handle": "session-a",
                    }
                },
            }
        )


def test_stdio_environment_is_explicit_and_missing_values_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("MONITOR_TOKEN", "secret")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-pass")
    config = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "transport": "stdio",
            "command": "python",
            "inherit_env": ["MONITOR_TOKEN"],
        }
    )
    environment = StdioMCPClient(config)._environment()
    assert environment["PATH"] == "/safe/bin"
    assert environment["MONITOR_TOKEN"] == "secret"
    assert "UNRELATED_SECRET" not in environment

    monkeypatch.delenv("MONITOR_TOKEN")
    with pytest.raises(ValueError, match="MONITOR_TOKEN"):
        StdioMCPClient(config)._environment()


@pytest.mark.asyncio
async def test_stdio_client_with_live_official_mcp_server(tmp_path: Path) -> None:
    pytest.importorskip("mcp.server")
    server_script = tmp_path / "server.py"
    server_script.write_text(
        """
from mcp.server import MCPServer

app = MCPServer("stdio-test")

@app.tool(name="monitor.echo")
def echo(message: str) -> str:
    return f"echo:{message}"

import asyncio
asyncio.run(app.run_stdio_async())
""".strip(),
        encoding="utf-8",
    )
    config = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(server_script)],
            "working_directory": str(tmp_path),
            "timeout_seconds": 5,
        }
    )
    client = StdioMCPClient(config)
    try:
        await client.start()
        tools = await client.list_tools()
        result = await client.call_tool("monitor.echo", {"message": "hello"})
    finally:
        await client.close()

    assert [tool.name for tool in tools] == ["monitor.echo"]
    assert result.structured_content == {"result": "echo:hello"}


@pytest.mark.asyncio
async def test_stdio_client_can_close_from_a_different_task(tmp_path: Path) -> None:
    pytest.importorskip("mcp.server")
    server_script = tmp_path / "cross_task_server.py"
    server_script.write_text(
        """
from mcp.server import MCPServer

app = MCPServer("stdio-cross-task")

@app.tool(name="monitor.echo")
def echo(message: str) -> str:
    return message

import asyncio
asyncio.run(app.run_stdio_async())
""".strip(),
        encoding="utf-8",
    )
    client = StdioMCPClient(
        MCPServerConfig.model_validate(
            {
                "enabled": True,
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server_script)],
                "working_directory": str(tmp_path),
                "timeout_seconds": 5,
            }
        )
    )
    await client.start()

    await asyncio.create_task(client.close())

    with pytest.raises(RuntimeError, match="not started"):
        await client.list_tools()


@pytest.mark.asyncio
async def test_streamable_http_client_with_live_local_mcp_server() -> None:
    uvicorn = pytest.importorskip("uvicorn")
    mcp_server = pytest.importorskip("mcp.server")
    MCPServer = mcp_server.MCPServer

    server_mcp = MCPServer("knoa-test")

    @server_mcp.tool()
    def echo(message: str) -> str:
        return f"echo:{message}"

    server = uvicorn.Server(
        uvicorn.Config(
            server_mcp.streamable_http_app(
                stateless_http=True,
                json_response=True,
            ),
            host="127.0.0.1",
            port=0,
            log_level="error",
        )
    )
    server_task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started or server_task.done():
            break
        await asyncio.sleep(0.01)
    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    client = StreamableHTTPMCPClient(
        f"http://127.0.0.1:{port}/mcp",
        timeout_seconds=5,
    )
    try:
        await client.start()
        tools = await client.list_tools()
        result = await client.call_tool("echo", {"message": "hello"})
    finally:
        await client.close()
        server.should_exit = True
        await server_task

    assert [tool.name for tool in tools] == ["echo"]
    assert result.structured_content == {"result": "echo:hello"}
