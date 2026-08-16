"""Run the optional self-hosted Hub and Relay process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from knoa_platform.hub.app import create_hub_app


def main() -> int:
    parser = argparse.ArgumentParser(prog="knoa-hub")
    parser.add_argument("--host", default=os.environ.get("KNOA_HUB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KNOA_HUB_PORT", "9530")))
    parser.add_argument("--root", default=os.environ.get("KNOA_HUB_ROOT", "~/.knoa/hub"))
    parser.add_argument("--hub-id", default=os.environ.get("KNOA_HUB_ID", "hub_personal"))
    args = parser.parse_args()
    token = os.environ.get("KNOA_HUB_OWNER_TOKEN", "")
    if len(token) < 32:
        parser.error("KNOA_HUB_OWNER_TOKEN must contain at least 32 characters")
    uvicorn.run(
        create_hub_app(Path(args.root), hub_id=args.hub_id, owner_token=token),
        host=args.host,
        port=args.port,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
