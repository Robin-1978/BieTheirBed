"""Run the optional self-hosted Hub and Relay process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from knoa_platform.hub.app import create_hub_app
from knoa_platform.hub.hosted import create_hosted_hub_app


def main() -> int:
    parser = argparse.ArgumentParser(prog="knoa-hub")
    parser.add_argument("--host", default=os.environ.get("KNOA_HUB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KNOA_HUB_PORT", "9530")))
    parser.add_argument("--root", default=os.environ.get("KNOA_HUB_ROOT", "~/.knoa/hub"))
    parser.add_argument("--hub-id", default=os.environ.get("KNOA_HUB_ID", "hub_personal"))
    parser.add_argument(
        "--deployment-mode",
        choices=("self_hosted", "hosted_single_node"),
        default=os.environ.get("KNOA_HUB_DEPLOYMENT_MODE", "self_hosted"),
    )
    args = parser.parse_args()
    if args.deployment_mode == "hosted_single_node":
        token = os.environ.get("KNOA_HUB_BOOTSTRAP_TOKEN", "")
        if len(token) < 32:
            parser.error(
                "KNOA_HUB_BOOTSTRAP_TOKEN must contain at least 32 characters"
            )
        application = create_hosted_hub_app(
            Path(args.root),
            hub_id=args.hub_id,
            bootstrap_token=token,
        )
    else:
        token = os.environ.get("KNOA_HUB_OWNER_TOKEN", "")
        if len(token) < 32:
            parser.error("KNOA_HUB_OWNER_TOKEN must contain at least 32 characters")
        application = create_hub_app(
            Path(args.root),
            hub_id=args.hub_id,
            owner_token=token,
        )
    uvicorn.run(
        application,
        host=args.host,
        port=args.port,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
