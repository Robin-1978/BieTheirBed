"""Session-scoped standard MCP facade over Platform capabilities."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import uvicorn
from mcp import ClientSession, types
from mcp.client._memory import InMemoryTransport
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel.server import Server, ServerRequestContext

from knoa_agent_contracts import McpEndpointGrant
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.agent_runtime.tool_step import (
    ConfirmationPort,
    ProposedToolCall,
    ToolCommitPort,
    ToolStep,
    ToolStepContext,
    ToolStepResult,
)
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.tools.base import ToolCapability
from knoa_platform.tools.registry import ToolRegistry

_GRANT_HEADER = "x-knoa-capability-grant"
_TOOL_CALL_ID_META = "io.knoa/tool-call-id"


class _EmbeddedUvicornServer(uvicorn.Server):
    """Keep process signal ownership with the Knoa Core daemon."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


@dataclass(frozen=True)
class CapabilityGrant:
    """Opaque short-lived authority used by one Agent Turn."""

    token: str
    scope: RuntimeScope
    run_id: str
    client_request_id: str
    capabilities: frozenset[ToolCapability]
    cancellation: asyncio.Event
    confirmation: ConfirmationPort | None
    tool_commit: ToolCommitPort | None
    interaction: Any
    binding_epoch: int
    expires_at: float
    scope_digest: str
    artifact_ids: frozenset[str]
    allow_tools: bool


class CapabilityGrantRegistry:
    """Process-local grant authority; tokens never enter model content."""

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._grants: dict[str, CapabilityGrant] = {}
        self._guard = asyncio.Lock()

    async def issue(
        self,
        *,
        scope: RuntimeScope,
        run_id: str,
        client_request_id: str,
        capabilities: frozenset[ToolCapability],
        cancellation: asyncio.Event,
        confirmation: ConfirmationPort | None,
        tool_commit: ToolCommitPort | None,
        interaction: Any = None,
        artifact_ids: frozenset[str] = frozenset(),
        binding_epoch: int = 1,
        ttl_seconds: float = 300.0,
        allow_tools: bool = True,
    ) -> CapabilityGrant:
        if ttl_seconds <= 0:
            raise ValueError("Capability grant TTL must be positive")
        canonical_scope = json.dumps(
            {
                "principal_id": scope.principal_id,
                "session_handle": scope.session_handle,
                "run_id": run_id,
                "binding_epoch": binding_epoch,
                "capabilities": sorted(item.value for item in capabilities),
                "artifact_ids": sorted(artifact_ids),
                "allow_tools": allow_tools,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        grant = CapabilityGrant(
            token=secrets.token_urlsafe(32),
            scope=scope,
            run_id=run_id,
            client_request_id=client_request_id,
            capabilities=capabilities,
            cancellation=cancellation,
            confirmation=confirmation,
            tool_commit=tool_commit,
            interaction=interaction,
            binding_epoch=binding_epoch,
            expires_at=self._clock() + ttl_seconds,
            scope_digest=hashlib.sha256(canonical_scope.encode()).hexdigest(),
            artifact_ids=artifact_ids,
            allow_tools=allow_tools,
        )
        async with self._guard:
            self._purge_expired_locked()
            self._grants[grant.token] = grant
        return grant

    async def revoke(self, token: str) -> None:
        async with self._guard:
            self._grants.pop(token, None)

    async def resolve(self, token: str) -> CapabilityGrant:
        async with self._guard:
            self._purge_expired_locked()
            grant = self._grants.get(token)
        if grant is None:
            raise PermissionError("Capability grant is invalid or expired")
        return grant

    def _purge_expired_locked(self) -> None:
        now = self._clock()
        expired = [
            token
            for token, grant in self._grants.items()
            if grant.expires_at <= now
        ]
        for token in expired:
            self._grants.pop(token, None)


class CapabilityGateway:
    """Official MCP Server projecting authorized Platform tool handlers."""

    def __init__(
        self,
        registry: ToolRegistry,
        tool_step: ToolStep,
        artifacts: ArtifactStore | None = None,
        grants: CapabilityGrantRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._tool_step = tool_step
        self._artifacts = artifacts
        self.grants = grants or CapabilityGrantRegistry()
        self.server = Server(
            "knoa-platform-capabilities",
            version="1.0.0",
            instructions=(
                "Session-scoped Knoa Platform capabilities. Tool availability "
                "and calls are authoritative only for the supplied grant."
            ),
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
        )

    @staticmethod
    def _token(context: ServerRequestContext[Any, Any]) -> str:
        access_token = get_access_token()
        if access_token is not None and access_token.token:
            return access_token.token
        request = context.request
        headers = getattr(request, "headers", None)
        if headers is not None:
            token = str(headers.get(_GRANT_HEADER, "")).strip()
            if token:
                return token
        meta = context.meta
        if isinstance(meta, dict):
            token = str(meta.get(_GRANT_HEADER, "")).strip()
            if token:
                return token
        raise PermissionError("Capability grant is required")

    async def _grant(
        self,
        context: ServerRequestContext[Any, Any],
    ) -> CapabilityGrant:
        return await self.grants.resolve(self._token(context))

    async def _list_tools(
        self,
        context: ServerRequestContext[Any, Any],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        grant = await self._grant(context)
        if not grant.allow_tools:
            return types.ListToolsResult(tools=[])
        tools = []
        for definition in self._registry.definitions_for(grant.capabilities):
            tools.append(
                types.Tool(
                    name=str(definition["name"]),
                    description=str(definition.get("description") or ""),
                    inputSchema=dict(definition["inputSchema"]),
                    outputSchema=(
                        dict(definition["outputSchema"])
                        if "outputSchema" in definition
                        else None
                    ),
                )
            )
        return types.ListToolsResult(tools=tools)

    async def _call_tool(
        self,
        context: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        grant = await self._grant(context)
        if not grant.allow_tools:
            raise PermissionError("This capability grant does not allow Tools")
        call_id = self._call_id(context, params)
        result = await self._tool_step.execute(
            ToolStepContext(
                scope=grant.scope,
                run_id=grant.run_id,
                client_request_id=grant.client_request_id,
                capabilities=grant.capabilities,
                cancellation=grant.cancellation,
                confirmation=grant.confirmation,
                commit=grant.tool_commit,
                interaction=grant.interaction,
            ),
            ProposedToolCall(
                call_id=call_id,
                name=params.name,
                arguments=dict(params.arguments or {}),
            ),
        )
        payload = result.model_dump(mode="json")
        return types.CallToolResult(
            content=[
                types.TextContent(
                    text=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                )
            ],
            structuredContent=payload,
            isError=result.status != "completed",
        )

    async def _list_resources(
        self,
        context: ServerRequestContext[Any, Any],
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListResourcesResult:
        grant = await self._grant(context)
        if self._artifacts is None:
            return types.ListResourcesResult(resources=[])
        resources = []
        for artifact_id in sorted(grant.artifact_ids):
            metadata = self._artifacts.metadata(
                grant.scope.session_handle,
                artifact_id,
            )
            resources.append(
                types.Resource(
                    uri=self.artifact_uri(artifact_id),
                    name=str(metadata["name"]),
                    description="Knoa Platform session artifact",
                    mimeType=str(metadata["media_type"]),
                    size=int(metadata["size"]),
                )
            )
        return types.ListResourcesResult(resources=resources)

    async def _read_resource(
        self,
        context: ServerRequestContext[Any, Any],
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult:
        grant = await self._grant(context)
        artifact_id = self.artifact_id(str(params.uri))
        if self._artifacts is None or artifact_id not in grant.artifact_ids:
            raise PermissionError("Artifact Resource is not authorized")
        metadata = self._artifacts.metadata(
            grant.scope.session_handle,
            artifact_id,
        )
        media_type = str(metadata["media_type"])
        if self._textual(media_type, str(metadata["name"])):
            value = self._artifacts.read_text(
                grant.scope.session_handle,
                artifact_id,
            )
            text = str(value["content"])
            if value["truncated"]:
                text += "\n\n[artifact content truncated by Platform]"
            contents: list[
                types.TextResourceContents | types.BlobResourceContents
            ] = [
                types.TextResourceContents(
                    uri=self.artifact_uri(artifact_id),
                    mimeType=media_type,
                    text=text,
                )
            ]
        else:
            data_url = self._artifacts.read_data_url(
                grant.scope.session_handle,
                artifact_id,
                max_bytes=int(metadata["size"]),
            )
            _prefix, _separator, encoded = data_url.partition(",")
            # Decode/re-encode rejects malformed backing data URL construction.
            blob = base64.b64encode(base64.b64decode(encoded)).decode("ascii")
            contents = [
                types.BlobResourceContents(
                    uri=self.artifact_uri(artifact_id),
                    mimeType=media_type,
                    blob=blob,
                )
            ]
        return types.ReadResourceResult(contents=contents)

    @staticmethod
    def artifact_uri(artifact_id: str) -> str:
        return f"knoa-artifact://{artifact_id}"

    @staticmethod
    def artifact_id(uri: str) -> str:
        prefix = "knoa-artifact://"
        if not uri.startswith(prefix):
            raise LookupError("Unknown Platform Artifact Resource URI")
        artifact_id = uri.removeprefix(prefix)
        if not artifact_id or any(character in artifact_id for character in "/?#"):
            raise ValueError("Invalid Platform Artifact Resource URI")
        return artifact_id

    @staticmethod
    def _textual(media_type: str, name: str) -> bool:
        return media_type.startswith("text/") or media_type in {
            "application/json",
            "application/javascript",
            "application/sql",
            "application/toml",
            "application/xml",
            "application/x-yaml",
        } or name.lower().endswith(
            (".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".xml", ".csv", ".log")
        )

    @staticmethod
    def _call_id(
        context: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
    ) -> str:
        request_id = context.request_id
        meta = context.meta
        if isinstance(meta, dict):
            supplied = str(meta.get(_TOOL_CALL_ID_META, "")).strip()
            if supplied:
                return supplied[:256]
        if request_id is not None:
            return f"mcp-{request_id}"[:256]
        digest = hashlib.sha256(
            json.dumps(
                {"name": params.name, "arguments": params.arguments or {}},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return f"mcp-{digest[:32]}"


class CapabilityGrantTokenVerifier(TokenVerifier):
    """Authenticate standard MCP HTTP requests with live capability grants."""

    def __init__(self, grants: CapabilityGrantRegistry) -> None:
        self._grants = grants

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            grant = await self._grants.resolve(token)
        except PermissionError:
            return None
        return AccessToken(
            token=token,
            client_id="knoa-agent-runtime",
            scopes=["knoa.capabilities"],
            expires_at=int(grant.expires_at),
            subject=grant.scope.principal_id,
        )


class CapabilityMCPHost:
    """Loopback-only standard Streamable HTTP host for out-of-process Agents."""

    def __init__(
        self,
        gateway: CapabilityGateway,
        *,
        host: str = "127.0.0.1",
        port: int = 9530,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Capability MCP host must bind to loopback")
        if not 0 <= port <= 65535:
            raise ValueError("Capability MCP port must be between 0 and 65535")
        self._host = host
        self._port = port
        resource_url = f"http://{host}:{port}/mcp"
        self.app = gateway.server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            host=host,
            auth=AuthSettings(
                issuer_url="https://knoa.invalid",
                resource_server_url=resource_url,
                required_scopes=["knoa.capabilities"],
            ),
            token_verifier=CapabilityGrantTokenVerifier(gateway.grants),
        )
        self._server: _EmbeddedUvicornServer | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def started(self) -> bool:
        return self._task is not None

    @property
    def bound_port(self) -> int | None:
        server = self._server
        if server is None or not server.servers:
            return None
        sockets = server.servers[0].sockets
        if not sockets:
            return None
        return int(sockets[0].getsockname()[1])

    @property
    def endpoint(self) -> str:
        port = self.bound_port
        if port is None:
            raise RuntimeError("Capability MCP host is not started")
        return f"http://{self._host}:{port}/mcp"

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Capability MCP host is already started")
        server = _EmbeddedUvicornServer(
            uvicorn.Config(
                self.app,
                host=self._host,
                port=self._port,
                log_config=None,
                access_log=False,
                lifespan="on",
            )
        )
        task = asyncio.create_task(server.serve(), name="knoa-capability-mcp")
        self._server = server
        self._task = task
        try:
            for _ in range(500):
                if server.started:
                    return
                if task.done():
                    await task
                    raise RuntimeError("Capability MCP host stopped during startup")
                await asyncio.sleep(0.01)
            raise TimeoutError("Capability MCP host startup timed out")
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        server, self._server = self._server, None
        task, self._task = self._task, None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class GatewayMCPClient:
    """Factory for Turn-scoped standard MCP ClientSession connections."""

    def __init__(self, gateway: CapabilityGateway) -> None:
        self._gateway = gateway

    @asynccontextmanager
    async def bind(self, grant: CapabilityGrant):
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                InMemoryTransport(self._gateway.server)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            yield BoundGatewayToolClient(session, grant)
        finally:
            await stack.aclose()

    @staticmethod
    def _meta(grant: CapabilityGrant) -> dict[str, str]:
        return {_GRANT_HEADER: grant.token}


class GatewayMCPConnector:
    """In-process transport adapter implementing Knoa Agent's MCP connector."""

    def __init__(self, gateway: CapabilityGateway) -> None:
        self._gateway = gateway
        self._client = GatewayMCPClient(gateway)

    @asynccontextmanager
    async def connect(self, endpoint: McpEndpointGrant):
        if endpoint.server_id != "knoa-platform-capabilities":
            raise PermissionError("Unknown MCP endpoint grant")
        if endpoint.transport != "in_memory":
            raise ValueError("In-process connector requires in_memory transport")
        grant = await self._gateway.grants.resolve(endpoint.authorization)
        if grant.binding_epoch != endpoint.binding_epoch:
            raise PermissionError("MCP endpoint binding epoch changed")
        if grant.scope_digest != endpoint.scope_digest:
            raise PermissionError("MCP endpoint scope digest changed")
        async with self._client.bind(grant) as client:
            yield client

class BoundGatewayToolClient:
    """Turn-scoped Agent view over one standard MCP ClientSession."""

    def __init__(
        self,
        session: ClientSession,
        grant: CapabilityGrant,
    ) -> None:
        self._session = session
        self._grant = grant

    async def list_tools(self) -> tuple[dict[str, Any], ...]:
        result = await self._session.list_tools(
            params=types.PaginatedRequestParams(
                _meta=GatewayMCPClient._meta(self._grant)
            )
        )
        definitions = []
        for tool in result.tools:
            definition: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": dict(tool.input_schema),
            }
            if tool.output_schema is not None:
                definition["outputSchema"] = dict(tool.output_schema)
            definitions.append(definition)
        return tuple(sorted(definitions, key=lambda item: str(item["name"])))

    async def call_tool(self, call: ProposedToolCall) -> ToolStepResult:
        result = await self._session.call_tool(
            call.name,
            call.arguments,
            meta={
                **GatewayMCPClient._meta(self._grant),
                _TOOL_CALL_ID_META: call.call_id,
            },
        )
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return ToolStepResult.model_validate(structured)
        raise RuntimeError("Gateway returned an invalid ToolStep result")

    async def list_resources(self) -> tuple[dict[str, Any], ...]:
        result = await self._session.list_resources(
            params=types.PaginatedRequestParams(
                _meta=GatewayMCPClient._meta(self._grant)
            )
        )
        return tuple(
            {
                "uri": str(resource.uri),
                "name": resource.name,
                "media_type": resource.mime_type or "application/octet-stream",
                "size": int(resource.size or 0),
            }
            for resource in result.resources
        )

    async def read_resource(self, uri: str) -> tuple[dict[str, Any], ...]:
        result = await self._session.read_resource(
            uri,
            meta=GatewayMCPClient._meta(self._grant),
        )
        contents = []
        for content in result.contents:
            text = getattr(content, "text", None)
            if text is not None:
                contents.append(
                    {
                        "uri": str(content.uri),
                        "media_type": content.mime_type or "text/plain",
                        "text": str(text),
                    }
                )
            else:
                contents.append(
                    {
                        "uri": str(content.uri),
                        "media_type": content.mime_type or "application/octet-stream",
                        "blob": str(getattr(content, "blob", "")),
                    }
                )
        return tuple(contents)
