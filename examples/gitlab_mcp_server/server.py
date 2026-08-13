"""Standard MCP stdio server for GitLab CI diagnosis and guarded retry."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import types
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import (
    InMemorySubscriptionBus,
    ListenHandler,
    ResourcesListChanged,
    ResourceUpdated,
)

try:
    from .gitlab_client import GitLabClient, GitLabSettings, GitLabStateStore
except ImportError:  # installed as a self-contained MCP package
    from gitlab_client import GitLabClient, GitLabSettings, GitLabStateStore

logger = logging.getLogger("gitlab-mcp-example")
_COLLECTION_URI = "gitlab://failed-pipelines"
_EVENT_PREFIX = "gitlab://failed-pipelines/events/"


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class GitLabMCPApplication:
    def __init__(self, settings: GitLabSettings) -> None:
        self.settings = settings
        self.store = GitLabStateStore(settings.state_path)
        self.gitlab = GitLabClient(settings, self.store)
        self._subscriptions = InMemorySubscriptionBus()
        self.server = Server(
            "gitlab-reference",
            version="1.0.0",
            instructions=(
                "Inspect bounded GitLab CI evidence and propose guarded retries. "
                "Retry tools require host approval and server-side enablement."
            ),
            lifespan=self._lifespan,
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
            on_subscriptions_listen=ListenHandler(self._subscriptions),
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )

    @asynccontextmanager
    async def _lifespan(self, _server: Server):
        try:
            await self.gitlab.poll_failure_events()
        except Exception:
            logger.exception("Initial GitLab poll failed")
        worker = asyncio.create_task(self._poll_loop(), name="gitlab-mcp-poller")
        try:
            yield self
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await self.gitlab.close()

    async def _poll_loop(self) -> None:
        while True:
            try:
                if await self.gitlab.poll_failure_events():
                    await self._subscriptions.publish(ResourcesListChanged())
                    await self._subscriptions.publish(
                        ResourceUpdated(uri=_COLLECTION_URI)
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GitLab polling failed")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _list_resources(self, _context: Any, _params: Any):
        resources = [
            types.Resource(
                uri=_COLLECTION_URI,
                name="Failed GitLab pipeline event inventory",
                description="Collection wake-up Resource; event members are immutable.",
                mime_type="text/markdown",
            )
        ]
        for event in self.store.list_failure_events():
            payload = event["payload"]
            resources.append(
                types.Resource(
                    uri=f"{_EVENT_PREFIX}{event['event_id']}",
                    name=(
                        f"Diagnose failed pipeline {payload['project']} "
                        f"#{payload['pipeline_id']}"
                    ),
                    description="Immutable task instruction for one failed CI state.",
                    mime_type="text/markdown",
                    annotations=types.Annotations(audience=["assistant"], priority=1.0),
                )
            )
        return types.ListResourcesResult(resources=resources)

    async def _read_resource(self, _context: Any, params: types.ReadResourceRequestParams):
        uri = params.uri
        if uri == _COLLECTION_URI:
            lines = ["# Failed GitLab pipeline events", ""]
            lines.extend(
                f"- {_EVENT_PREFIX}{event['event_id']}"
                for event in self.store.list_failure_events()
            )
            text = "\n".join(lines)
        elif uri.startswith(_EVENT_PREFIX):
            event_id = uri.removeprefix(_EVENT_PREFIX)
            if not event_id or "/" in event_id:
                raise ValueError("Invalid GitLab failure event URI")
            event = self.store.get_failure_event(event_id)
            if event is None:
                raise LookupError("GitLab failure event was not found")
            payload = event["payload"]
            text = (
                "Diagnose this failed GitLab CI state. Use the read-only GitLab MCP "
                "Tools to inspect the pipeline, failed jobs and bounded job traces. "
                "If an authorized workspace contains the matching repository, inspect "
                "the referenced branch and commit there. Classify the result as retry, "
                "stop or needs_human. Prefer retrying one job. Do not propose another "
                "retry for the same deterministic failure fingerprint; after two failed "
                "attempts, diagnose before proposing any retry. Every retry remains a "
                "high-risk host-approved MCP Tool call.\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )
        else:
            raise LookupError("Unknown GitLab Resource URI")
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, text=text, mime_type="text/markdown")]
        )

    async def _list_tools(self, _context: Any, _params: Any):
        read_only = types.ToolAnnotations(read_only_hint=True, open_world_hint=True)
        retry = types.ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
        tools = []
        for name, identifier, description in (
            ("gitlab.get_pipeline", "pipeline_id", "Read one configured GitLab pipeline."),
            ("gitlab.list_pipeline_jobs", "pipeline_id", "List jobs for one configured pipeline."),
            ("gitlab.get_job", "job_id", "Read one configured GitLab CI job."),
        ):
            tools.append(
                types.Tool(
                    name=name,
                    description=description,
                    input_schema=_schema(
                        {
                            "project": {"type": "string"},
                            identifier: {"type": ["string", "integer"]},
                        },
                        ["project", identifier],
                    ),
                    annotations=read_only,
                )
            )
        tools.append(
            types.Tool(
                name="gitlab.get_job_trace",
                description="Read a bounded tail of one configured GitLab CI job trace.",
                input_schema=_schema(
                    {
                        "project": {"type": "string"},
                        "job_id": {"type": ["string", "integer"]},
                        "tail_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
                        "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 1048576},
                    },
                    ["project", "job_id"],
                ),
                annotations=read_only,
            )
        )
        for name, identifier, description in (
            ("gitlab.retry_pipeline", "pipeline_id", "Retry a configured GitLab pipeline after host approval."),
            ("gitlab.retry_job", "job_id", "Retry one configured GitLab CI job after host approval."),
        ):
            tools.append(
                types.Tool(
                    name=name,
                    description=description,
                    input_schema=_schema(
                        {
                            "project": {"type": "string"},
                            identifier: {"type": ["string", "integer"]},
                            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 128},
                        },
                        ["project", identifier, "idempotency_key"],
                    ),
                    annotations=retry,
                )
            )
        return types.ListToolsResult(tools=tools)

    async def _call_tool(self, _context: Any, params: types.CallToolRequestParams):
        name = params.name
        args = params.arguments or {}
        project = str(args.get("project", ""))
        try:
            if name == "gitlab.get_pipeline":
                payload = await self.gitlab.get_pipeline(project, str(args.get("pipeline_id", "")))
            elif name == "gitlab.list_pipeline_jobs":
                payload = {"jobs": await self.gitlab.list_pipeline_jobs(project, str(args.get("pipeline_id", "")))}
            elif name == "gitlab.get_job":
                payload = await self.gitlab.get_job(project, str(args.get("job_id", "")))
            elif name == "gitlab.get_job_trace":
                payload = await self.gitlab.get_job_trace(
                    project,
                    str(args.get("job_id", "")),
                    tail_lines=int(args.get("tail_lines", 400)),
                    max_bytes=int(args.get("max_bytes", 131_072)),
                )
            elif name == "gitlab.retry_pipeline":
                payload = await self.gitlab.retry_pipeline(
                    project,
                    str(args.get("pipeline_id", "")),
                    str(args.get("idempotency_key", "")),
                )
            elif name == "gitlab.retry_job":
                payload = await self.gitlab.retry_job(
                    project,
                    str(args.get("job_id", "")),
                    str(args.get("idempotency_key", "")),
                )
            else:
                raise LookupError("Unknown GitLab tool")
            structured = json.loads(json.dumps(payload, ensure_ascii=False))
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(structured, ensure_ascii=False))],
                structured_content=structured,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - convert provider errors to MCP
            logger.warning("GitLab MCP tool failed: %s (%s)", name, type(exc).__name__)
            return types.CallToolResult(
                content=[types.TextContent(text=f"{type(exc).__name__}: {str(exc)[:1000]}")],
                is_error=True,
            )

    def initialization_options(self):
        return self.server.create_initialization_options(
            NotificationOptions(resources_changed=True)
        )

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.initialization_options())


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await GitLabMCPApplication(GitLabSettings.from_env()).run_stdio()


if __name__ == "__main__":
    asyncio.run(_main())
