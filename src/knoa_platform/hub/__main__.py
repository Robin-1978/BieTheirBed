"""Run the optional self-hosted Hub and Relay process."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

import uvicorn

from knoa_platform.hub.app import create_hub_app
from knoa_platform.hub.hosted import HostedHubApplication
from knoa_platform.network_tls import is_loopback_host


class _EmbeddedUvicornServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def _serve_with_console(
    public_app,
    console_app,
    *,
    host: str,
    port: int,
    console_host: str,
    console_port: int,
) -> None:
    console = _EmbeddedUvicornServer(
        uvicorn.Config(
            console_app,
            host=console_host,
            port=console_port,
            access_log=False,
        )
    )
    public = uvicorn.Server(
        uvicorn.Config(public_app, host=host, port=port, access_log=False)
    )
    console_task = asyncio.create_task(console.serve())
    try:
        await public.serve()
    finally:
        console.should_exit = True
        await console_task


def main() -> int:
    parser = argparse.ArgumentParser(prog="knoa-hub")
    parser.add_argument("--host", default=os.environ.get("KNOA_HUB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("KNOA_HUB_PORT", "9530"))
    )
    parser.add_argument(
        "--console-host",
        default=os.environ.get("KNOA_HUB_CONSOLE_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--console-port",
        type=int,
        default=int(os.environ.get("KNOA_HUB_CONSOLE_PORT", "9532")),
    )
    parser.add_argument(
        "--root", default=os.environ.get("KNOA_HUB_ROOT", "~/.knoa/hub")
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("KNOA_HUB_PUBLIC_URL", "http://127.0.0.1:9529"),
    )
    parser.add_argument(
        "--hub-id", default=os.environ.get("KNOA_HUB_ID", "hub_personal")
    )
    parser.add_argument(
        "--deployment-mode",
        choices=("self_hosted", "hosted_single_node"),
        default=os.environ.get("KNOA_HUB_DEPLOYMENT_MODE", "self_hosted"),
    )
    args = parser.parse_args()
    if not is_loopback_host(args.console_host):
        parser.error("Hub Console must bind to a loopback address")
    if args.console_port == args.port and args.console_host == args.host:
        parser.error("Hub public and Console listeners must be distinct")
    if args.deployment_mode == "hosted_single_node":
        token = os.environ.get("KNOA_HUB_BOOTSTRAP_TOKEN", "")
        if len(token) < 32:
            parser.error("KNOA_HUB_BOOTSTRAP_TOKEN must contain at least 32 characters")
        composition = HostedHubApplication(
            Path(args.root),
            hub_id=args.hub_id,
            bootstrap_token=token,
            release_publish_token=os.environ.get("KNOA_HUB_RELEASE_PUBLISH_TOKEN", ""),
            public_url=args.public_url,
        )
        asyncio.run(
            _serve_with_console(
                composition.app,
                composition.console_app,
                host=args.host,
                port=args.port,
                console_host=args.console_host,
                console_port=args.console_port,
            )
        )
        return 0
    else:
        token = os.environ.get("KNOA_HUB_OWNER_TOKEN", "")
        if len(token) < 32:
            parser.error("KNOA_HUB_OWNER_TOKEN must contain at least 32 characters")
        application = create_hub_app(
            Path(args.root),
            hub_id=args.hub_id,
            owner_token=token,
        )
    uvicorn.run(application, host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
