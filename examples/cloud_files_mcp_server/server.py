"""Standard MCP stdio server for cloud evidence files (OSS + Tempo)."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import types
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

try:
    from .cloud_client import CloudFilesClient, CloudFilesSettings
except ImportError:  # installed as a self-contained MCP package
    from cloud_client import CloudFilesClient, CloudFilesSettings

logger = logging.getLogger("cloud-files-mcp-example")


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class CloudFilesMCPApplication:
    def __init__(self, settings: CloudFilesSettings) -> None:
        self.settings = settings
        self.client = CloudFilesClient(settings)
        self.server = Server(
            "cloud-files-reference",
            version="1.0.0",
            instructions=(
                "List and download cloud evidence files from OSS buckets and "
                "Tempo shared robot records. Downloads land under a caller "
                "case directory. Write tools require host approval."
            ),
            lifespan=self._lifespan,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )

    @asynccontextmanager
    async def _lifespan(self, _server: Server):
        try:
            yield self
        finally:
            pass

    async def _list_tools(self, _context: Any, _params: Any) -> types.ListToolsResult:
        read_only = types.ToolAnnotations(read_only_hint=True, open_world_hint=True)
        download = types.ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        )
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="cloud_files.list_files",
                    description=(
                        "Browse cloud evidence files. source=oss lists an "
                        "oss://gs-public-shared/ prefix; source=tempo queries a "
                        "Tempo shared-record-list link (needs share_id and sn)."
                    ),
                    input_schema=_schema(
                        {
                            "source": {"type": "string", "enum": ["oss", "tempo"]},
                            "prefix": {"type": "string"},
                            "share_id": {"type": "string"},
                            "sn": {"type": "string"},
                            "status": {"type": "string"},
                            "file_type": {"type": "string"},
                            "filename": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        ["source"],
                    ),
                    annotations=read_only,
                ),
                types.Tool(
                    name="cloud_files.download_file",
                    description=(
                        "Fetch one cloud evidence file into a caller case "
                        "directory after host approval. Tempo downloads also "
                        "need filename."
                    ),
                    input_schema=_schema(
                        {
                            "source": {"type": "string", "enum": ["oss", "tempo"]},
                            "url": {"type": "string"},
                            "filename": {"type": "string"},
                            "case_id": {"type": "string"},
                        },
                        ["source", "url"],
                    ),
                    annotations=download,
                ),
            ]
        )

    async def _call_tool(
        self, _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        try:
            if name == "cloud_files.list_files":
                payload: dict[str, Any] = await self.client.list_files(
                    str(arguments.get("source", "")),
                    prefix=str(arguments.get("prefix", "")),
                    share_id=str(arguments.get("share_id", "")),
                    sn=str(arguments.get("sn", "")),
                    status=arguments.get("status"),
                    file_type=arguments.get("file_type"),
                    filename=arguments.get("filename"),
                    limit=int(arguments.get("limit", 100)),
                )
            elif name == "cloud_files.download_file":
                payload = await self.client.download_file(
                    str(arguments.get("source", "")),
                    str(arguments.get("url", "")),
                    filename=str(arguments.get("filename", "")),
                    case_id=str(arguments.get("case_id", "")),
                )
            else:
                raise LookupError("Unknown cloud_files tool")
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(payload, ensure_ascii=False, default=str))],
                structured_content=payload,
            )
        except asyncio.CancelledError:
            raise
        except (LookupError, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    def initialization_options(self):
        return self.server.create_initialization_options(
            NotificationOptions(resources_changed=False)
        )

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.initialization_options())


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await CloudFilesMCPApplication(CloudFilesSettings.from_env()).run_stdio()


if __name__ == "__main__":
    asyncio.run(_main())
