"""Run the forward-only Core service."""
from __future__ import annotations

import argparse
import sys

from pc_assistant.branding import ASSISTANT_NAME


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{ASSISTANT_NAME} Service")
    parser.add_argument("--daemon", action="store_true", help="Daemonize")
    parser.add_argument("--config", type=str, default=None, help="Config path")
    parser.add_argument(
        "--log-dir", type=str, default=None,
        help="Directory for the service log file (default: runtime dir)",
    )
    args = parser.parse_args()

    from pc_assistant.service.application_daemon import run_service
    from pc_assistant.service.core_daemon import resolve_core_log

    log_path = resolve_core_log(args.log_dir, args.config)
    sys.exit(
        run_service(
            args.config,
            daemon=args.daemon,
            log_path=log_path,
        )
    )


if __name__ == "__main__":
    main()
