from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pc-assistant",
        description="PC Assistant - A Python computer assistant agent",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Path to configuration YAML file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="Print version and exit",
    )
    parser.add_argument(
        "-a", "--ask",
        type=str, default=None,
        help="Ask a single question and exit (benchmark mode). Use '-' to read from stdin.",
    )
    parser.add_argument(
        "--json",
        action="store_true", default=False,
        help="Output result as JSON (requires --ask)",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true", default=False,
        help="Disable tool execution (requires --ask)",
    )
    parser.add_argument(
        "-b", "--benchmark",
        type=str, default=None,
        help="Run a benchmark dataset (JSONL file or directory)",
    )
    parser.add_argument(
        "--benchmark-report",
        type=str, default=None,
        help="Generate report from benchmark results directory",
    )
    parser.add_argument(
        "--categories",
        type=str, default=None,
        help="Comma-separated benchmark categories to run (requires --benchmark)",
    )
    parser.add_argument(
        "--output",
        type=str, default=None,
        help="Output file for benchmark results (requires --benchmark)",
    )
    parser.add_argument(
        "--serve",
        action="store_true", default=False,
        help="Start the background service daemon",
    )
    parser.add_argument(
        "--daemon",
        action="store_true", default=False,
        help="Daemonize the service (detach from terminal)",
    )
    parser.add_argument(
        "--stop",
        action="store_true", default=False,
        help="Stop a running service daemon",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from pc_assistant import __version__

        print(f"pc_assistant {__version__}")
        return 0

    if args.json and not args.ask:
        parser.error("--json requires --ask")
    if args.no_tools and not args.ask:
        parser.error("--no-tools requires --ask")
    if args.categories and not args.benchmark:
        parser.error("--categories requires --benchmark")
    if args.output and not args.benchmark:
        parser.error("--output requires --benchmark")

    config_path = args.config
    if config_path is not None:
        config_path = str(Path(config_path).resolve())

    from pc_assistant import async_main, async_benchmark, async_benchmark_report

    if args.stop:
        return _stop_service()

    if args.serve:
        from pc_assistant.service.server import run_server
        return asyncio.run(run_server(config_path, daemon=args.daemon))

    if args.benchmark_report:
        return async_benchmark_report(args.benchmark_report)

    if args.benchmark:
        categories = args.categories.split(",") if args.categories else None
        return asyncio.run(async_benchmark(
            config_path, args.verbose, args.benchmark,
            categories=categories, output_path=args.output,
        ))

    try:
        return asyncio.run(async_main(
            config_path, args.verbose,
            ask=args.ask, json_output=args.json, no_tools=args.no_tools,
        ))
    except KeyboardInterrupt:
        return 130


def _stop_service() -> int:
    import os
    import signal
    from pc_assistant.service.protocol import PID_PATH

    if not PID_PATH.exists():
        print("Service is not running (no PID file)")
        return 1
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to service (pid {pid})")
        return 0
    except (ValueError, OSError) as e:
        print(f"Failed to stop service: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
