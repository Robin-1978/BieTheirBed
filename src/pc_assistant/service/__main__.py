"""Allow running the service as ``python -m pc_assistant.service.server``."""
from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="PC Assistant Service")
    parser.add_argument("--daemon", action="store_true", help="Daemonize")
    parser.add_argument("--config", type=str, default=None, help="Config path")
    args = parser.parse_args()

    from pc_assistant.service.server import run_server
    asyncio.run(run_server(args.config, daemon=args.daemon))


if __name__ == "__main__":
    main()
