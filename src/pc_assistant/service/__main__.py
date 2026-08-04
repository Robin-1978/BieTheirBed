"""Allow running the service as ``python -m pc_assistant.service.server``."""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="PC Assistant Service")
    parser.add_argument("--daemon", action="store_true", help="Daemonize")
    parser.add_argument("--config", type=str, default=None, help="Config path")
    parser.add_argument(
        "--log-dir", type=str, default=None,
        help="Directory for the service log file (default: runtime dir)",
    )
    args = parser.parse_args()

    from pc_assistant.service.server import run_server, resolve_service_log

    log_path = resolve_service_log(args.log_dir)
    sys.exit(run_server(args.config, daemon=args.daemon, log_path=log_path))


if __name__ == "__main__":
    main()
