from __future__ import annotations

import argparse

from knoa_platform.branding import (
    ASSISTANT_IDENTITY,
    ASSISTANT_NAME,
    ASSISTANT_NAME_EN,
)

__version__ = "0.2.13"


def _gateway_ttl(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("TTL must be an integer") from exc
    if not 30 <= parsed <= 900:
        raise argparse.ArgumentTypeError("TTL must be between 30 and 900 seconds")
    return parsed


def _positive_version_code(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Version code must be an integer") from exc
    if not 1 <= parsed <= 2_100_000_000:
        raise argparse.ArgumentTypeError(
            "Version code must be between 1 and 2100000000"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knoa",
        description=f"{ASSISTANT_IDENTITY} - A Python computer assistant agent",
    )
    parser.add_argument(
        "-c", "--config", type=str, default=None, help="Path to configuration YAML file"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help="Enable verbose (DEBUG) logging"
    )
    parser.add_argument(
        "--version", action="store_true", default=False, help="Print version and exit"
    )
    parser.add_argument(
        "-a", "--ask", type=str, default=None,
        help="Ask a single question and exit (benchmark mode). Use '-' to read from stdin.",
    )
    parser.add_argument(
        "--json", action="store_true", default=False,
        help="Output result as JSON (requires --ask)",
    )
    parser.add_argument(
        "--no-tools", action="store_true", default=False,
        help="Disable tool execution (requires --ask)",
    )
    parser.add_argument(
        "--attach", type=str, default=None, nargs="+",
        help="Image file path(s) to attach to the ask/turn (multimodal).",
    )
    parser.add_argument(
        "--agent", type=str, default=None,
        help="Agent ID for a new ask or TUI conversation.",
    )
    parser.add_argument(
        "-b", "--benchmark", type=str, default=None,
        help="Run a benchmark dataset (JSONL file or directory)",
    )
    parser.add_argument(
        "--benchmark-report", type=str, default=None,
        help="Generate report from benchmark results directory",
    )
    parser.add_argument(
        "--categories", type=str, default=None,
        help="Comma-separated benchmark categories to run (requires --benchmark)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output file for benchmark results (requires --benchmark)",
    )
    parser.add_argument(
        "--serve", action="store_true", default=False, help="Start the background service daemon"
    )
    parser.add_argument(
        "--start", action="store_true", default=False,
        help="Start the background service daemon if it is not already running",
    )
    parser.add_argument(
        "--daemon", action="store_true", default=False, help="Daemonize the service"
    )
    parser.add_argument(
        "--log-dir", type=str, default=None,
        help="Directory for the service log file (default: runtime dir)",
    )
    parser.add_argument(
        "--stop", action="store_true", default=False, help="Stop a running service daemon"
    )
    parser.add_argument(
        "--restart", action="store_true", default=False,
        help="Stop the service daemon (if running), then start it again",
    )
    parser.add_argument(
        "--status", action="store_true", default=False,
        help="Show whether the service daemon is running",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("agents", help="List enabled Agents")
    tasks = commands.add_parser("tasks", help="List durable product Tasks")
    tasks.add_argument("--limit", type=int, default=50)
    task = commands.add_parser("task", help="Show one durable product Task")
    task.add_argument("task_id")
    task_state = commands.add_parser("task-state", help="Change a durable Task state")
    task_state.add_argument("task_id")
    task_state.add_argument("state", choices=("active", "paused", "archived"))
    task_delete = commands.add_parser("task-delete", help="Delete a durable Task")
    task_delete.add_argument("task_id")
    executions = commands.add_parser("executions", help="List a Task's Executions")
    executions.add_argument("task_id")
    execution = commands.add_parser("execution", help="Show one Task Execution")
    execution.add_argument("execution_id")
    cancel_execution = commands.add_parser(
        "execution-cancel", help="Cancel a running Task Execution"
    )
    cancel_execution.add_argument("execution_id")
    cancel_execution.add_argument("--reason", default="")
    approve = commands.add_parser("approve", help="Approve a pending tool call")
    approve.add_argument("approval_id")
    deny = commands.add_parser("deny", help="Deny a pending tool call")
    deny.add_argument("approval_id")
    resolve = commands.add_parser("resolve", help="Resolve a pending HumanInteraction")
    resolve.add_argument("interaction_id")
    resolve.add_argument("value", help="Resolution JSON")
    follow_up = commands.add_parser("follow-up", help="Continue a durable Task")
    follow_up.add_argument("task_id")
    follow_up.add_argument("input")
    commands.add_parser("mcp-resources", help="List discovered MCP Resources")
    create_event = commands.add_parser(
        "task-create-event",
        help="Create a durable Task triggered by an MCP Resource",
    )
    create_event.add_argument("server_id")
    create_event.add_argument("resource_uri")
    create_event.add_argument("goal")
    create_event.add_argument("--title", default="")
    create_event.add_argument("--agent-id", default=None)
    create_event.add_argument("--include-descendants", action="store_true")
    create_event.add_argument("--descendants-only", action="store_true")
    set_event = commands.add_parser(
        "task-set-event",
        help="Change an existing Task to an MCP Resource trigger",
    )
    set_event.add_argument("task_id")
    set_event.add_argument("server_id")
    set_event.add_argument("resource_uri")
    set_event.add_argument("--include-descendants", action="store_true")
    set_event.add_argument("--descendants-only", action="store_true")
    mcp_deploy = commands.add_parser(
        "mcp-package-deploy",
        help="Explicitly deploy a local MCP package without invoking an Agent",
    )
    mcp_deploy.add_argument("path")
    mcp_deploy.add_argument("server_id")
    gateway = commands.add_parser(
        "gateway",
        help="Manage Secure Gateway pairing and devices locally",
    )
    gateway_commands = gateway.add_subparsers(
        dest="gateway_command",
        required=True,
    )
    pair = gateway_commands.add_parser("pair", help="Create a single-use pairing grant")
    pair.add_argument("--principal", default=None)
    pair.add_argument("--ttl", type=_gateway_ttl, default=300)
    devices = gateway_commands.add_parser("devices", help="List paired devices")
    devices.add_argument("--principal", default=None)
    revoke = gateway_commands.add_parser("revoke", help="Revoke one paired device")
    revoke.add_argument("device_id")
    revoke.add_argument("--principal", default=None)
    release = gateway_commands.add_parser(
        "release",
        help="Manage the private Android update channel",
    )
    release_commands = release.add_subparsers(
        dest="release_command",
        required=True,
    )
    publish = release_commands.add_parser("publish", help="Publish one immutable APK")
    publish.add_argument("apk_path")
    publish.add_argument(
        "--version-name",
        default="",
        help="Override the version name read from the APK manifest",
    )
    publish.add_argument(
        "--version-code",
        type=_positive_version_code,
        default=0,
        help="Override the version code read from the APK manifest",
    )
    publish.add_argument("--min-version-code", type=_positive_version_code, default=1)
    publish.add_argument("--notes", default="")
    release_commands.add_parser("latest", help="Show the latest published APK")
    return parser


async def async_main(
    config_path: str | None,
    verbose: bool,
    ask: str | None = None,
    json_output: bool = False,
    no_tools: bool = False,
    attach: list[str] | None = None,
    agent_id: str | None = None,
) -> int:
    import logging

    from knoa_platform.config import load_config
    from knoa_platform.logger import get_logger

    cfg = load_config(config_path)
    try:
        main_model = cfg.resolve_model()
    except ValueError as exc:
        print(f"ERROR: Invalid model configuration: {exc}")
        return 1

    if verbose:
        logging.getLogger("knoa_platform").setLevel(logging.DEBUG)

    logger = get_logger("main")
    logger.info("%s starting (config=%s)", ASSISTANT_NAME, config_path or "default")

    providers_needing_key = {"openai", "anthropic"}
    if main_model.driver in providers_needing_key and not main_model.api_key:
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            console.print(
                Panel(
                    f"Provider '{main_model.provider_name}' requires an API key.\n"
                    "Please set KNOA_LLM_API_KEY environment variable\n"
                    "or add llm_api_key to your config file.",
                    title="[red]✗ Missing API Key[/red]",
                    border_style="red",
                    expand=False,
                )
            )
        except ImportError:
            print(f"ERROR: Provider '{main_model.provider_name}' requires an API key.")
            print("Please set KNOA_LLM_API_KEY environment variable or add llm_api_key to your config file.")
        return 1

    if ask is not None:
        from knoa_platform.cli_core import run_core_ask

        return await run_core_ask(
            cfg,
            ask,
            json_output=json_output,
            no_tools=no_tools,
            attachments=attach,
            agent_id=agent_id,
        )

    from knoa_platform.service.core_lifecycle import get_core_client
    from knoa_platform.ui.core_app import CoreChatApp

    try:
        client = await get_core_client(cfg)
        health = await client.health()
        if not health.healthy:
            print(f"ERROR: {health.detail or 'No configured model is available'}")
            await client.disconnect()
            return 1
        session_handle = (
            await client.create_session(agent_id=agent_id)
            if agent_id
            else await client.create_session()
        )
    except Exception as exc:
        print(f"ERROR: Could not connect to Core service: {exc}")
        return 1

    chat_ui = (
        CoreChatApp(cfg, client, session_handle, agent_id=agent_id)
        if agent_id
        else CoreChatApp(cfg, client, session_handle)
    )

    try:
        await chat_ui.run_async()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down")
        try:
            from rich.console import Console

            Console().print("\n[dim]Interrupted. Goodbye![/dim]")
        except ImportError:
            print("\nInterrupted. Goodbye!")
    finally:
        await client.disconnect()

    return 0


async def async_benchmark(
    config_path: str | None,
    verbose: bool,
    benchmark_path: str,
    categories: list[str] | None = None,
    output_path: str | None = None,
) -> int:
    from knoa_platform.benchmark.runner import BenchmarkRunner
    from knoa_platform.config import load_config

    cfg = load_config(config_path)

    runner = BenchmarkRunner(config=cfg, output_path=output_path)

    import os
    if os.path.isdir(benchmark_path):
        results = await runner.run_all(benchmark_path, categories=categories)
    else:
        results = await runner.run_dataset(benchmark_path)

    print(f"\nDone. {len(results)} questions evaluated.")
    total = sum(r.weighted_score for r in results)
    total_weight = sum(r.weight for r in results)
    overall = total / total_weight if total_weight > 0 else 0.0
    print(f"Overall score: {overall:.2f}")

    if output_path:
        print(f"Results saved to {output_path}")

    return 0


def async_benchmark_report(results_dir: str) -> int:
    import json
    import sys
    from pathlib import Path

    from knoa_platform.benchmark.reporter import Reporter
    from knoa_platform.benchmark.types import BenchmarkResult

    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        print(f"Error: not a directory: {results_dir}", file=sys.stderr)
        return 1

    results: list[BenchmarkResult] = []
    for jsonl in sorted(results_dir.glob("*.jsonl")):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(BenchmarkResult.model_validate(json.loads(line)))
                    except Exception:
                        pass

    if not results:
        print(f"Error: no results found in {results_dir}", file=sys.stderr)
        return 1

    report_path = Reporter.generate_report(results, str(results_dir))
    print(f"Report generated: {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import asyncio
    from pathlib import Path

    from knoa_platform.network_tls import ensure_default_ca_bundle

    ensure_default_ca_bundle()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{ASSISTANT_NAME_EN} {__version__}")
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

    if args.command == "gateway":
        if args.gateway_command == "release":
            from knoa_platform.gateway.release_admin import run_android_release_admin

            return run_android_release_admin(
                config_path,
                action=args.release_command,
                apk_path=getattr(args, "apk_path", ""),
                version_name=getattr(args, "version_name", ""),
                version_code=getattr(args, "version_code", 0),
                min_version_code=getattr(args, "min_version_code", 1),
                notes=getattr(args, "notes", ""),
            )
        from knoa_platform.gateway.admin import run_gateway_admin

        return run_gateway_admin(
            config_path,
            action=args.gateway_command,
            principal_id=getattr(args, "principal", None),
            ttl_seconds=getattr(args, "ttl", 300),
            device_id=getattr(args, "device_id", ""),
        )

    if args.command in {
        "agents", "tasks", "task", "task-state", "task-delete",
        "executions", "execution", "execution-cancel",
        "approve", "deny", "resolve", "follow-up", "mcp-package-deploy",
        "mcp-resources", "task-create-event", "task-set-event",
    }:
        from knoa_platform.cli_management import run_client_command
        from knoa_platform.config import load_config

        command_values = vars(args).copy()
        command_values.pop("command", None)
        command_values.pop("config", None)
        return asyncio.run(
            run_client_command(
                load_config(config_path),
                args.command,
                **command_values,
            )
        )

    if args.status:
        return _service_status()

    if args.start:
        return _start_service(config_path, args.log_dir)

    if args.stop:
        return _stop_service()

    if args.restart:
        return _restart_service(config_path, args.log_dir)

    if args.serve:
        from knoa_platform.service.application_daemon import run_service
        from knoa_platform.service.core_daemon import resolve_core_log

        log_path = resolve_core_log(args.log_dir, config_path)
        return run_service(
            config_path,
            daemon=args.daemon,
            log_path=log_path,
        )

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
            attach=args.attach,
            agent_id=args.agent,
        ))
    except KeyboardInterrupt:
        return 130


def _service_state() -> tuple[bool, int | None]:
    """Return ``(running, pid)`` for the service daemon.

    Uses the PID file when valid; otherwise falls back to scanning the
    configured service TCP port so an orphaned daemon (no/ stale PID file)
    is still detected and can be stopped by ``--stop`` / ``--restart``.
    """
    import os

    from knoa_platform.runtime import RuntimePaths

    pid_path = RuntimePaths.from_root().pid

    pid: int | None = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().split()[0])
        except (ValueError, IndexError, OSError):
            pid = None
    if pid is not None:
        try:
            os.kill(pid, 0)
            return True, pid
        except OSError:
            pid = None

    port = _service_port()
    if port > 0:
        owners = _pids_listening_on_port(port)
        service_owners = [owner for owner in owners if _is_knoa_service_pid(owner)]
        if service_owners:
            return True, service_owners[0]

    if pid_path.exists():
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
    return False, None


def _is_knoa_service_pid(pid: int) -> bool:
    """Reject unrelated processes that happen to own Knoa's configured port."""
    from pathlib import Path

    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    arguments = tuple(
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    )
    for index, argument in enumerate(arguments[:-1]):
        if argument != "-m":
            continue
        module = arguments[index + 1]
        if module == "knoa_platform.service":
            return True
        if module == "knoa_platform" and "--serve" in arguments[index + 2 :]:
            return True
    return False


def _service_port() -> int:
    """The configured service TCP port (0 means TCP disabled)."""
    try:
        from knoa_platform.config import load_config

        return int(load_config().service_port)
    except Exception:
        return 0


def _pids_listening_on_port(port: int) -> list[int]:
    """Find PIDs listening on a TCP port via a Linux ``/proc`` scan."""
    import os
    import re

    if port <= 0 or not os.path.isdir("/proc"):
        return []

    target_inodes: set[int] = set()
    for netfile in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(netfile, encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) < 10 or parts[3] != "0A":  # 0A = LISTEN
                        continue
                    try:
                        if int(parts[1].split(":")[1], 16) == port:
                            target_inodes.add(int(parts[9]))
                    except (IndexError, ValueError):
                        continue
        except OSError:
            continue
    if not target_inodes:
        return []

    pids: set[int] = set()
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            fd_dir = f"/proc/{entry}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    link = os.readlink(f"{fd_dir}/{fd}")
                    m = re.fullmatch(r"socket:\[(\d+)\]", link)
                    if m and int(m.group(1)) in target_inodes:
                        pids.add(int(entry))
            except OSError:
                continue
    except OSError:
        pass
    return sorted(pids)


def _service_log_path() -> str:
    """The log path recorded by the daemon (from the PID file), else the default."""
    from knoa_platform.runtime import RuntimePaths

    paths = RuntimePaths.from_root()
    if paths.pid.exists():
        try:
            lines = paths.pid.read_text().splitlines()
            if len(lines) >= 2 and lines[1].strip():
                return lines[1].strip()
        except OSError:
            pass
    return str(paths.logs / "service.log")


def _wait_for_stopped(pid: int | None, timeout: float = 15.0) -> bool:
    import os
    import time

    if pid is None:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(1)
    return False


def _wait_for_running(pid: int | None, timeout: float = 30.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running, alive_pid = _service_state()
        if running and (pid is None or alive_pid == pid):
            return True
        time.sleep(1)
    return False


def _service_status() -> int:
    running, pid = _service_state()
    if running:
        print(f"Service is running (pid {pid}).")
        print(f"Log: {_service_log_path()}")
        return 0
    print("Service is not running.")
    return 1


def _stop_service() -> int:
    import os
    import signal
    import sys as _sys

    from knoa_platform.runtime import RuntimePaths

    pid_path = RuntimePaths.from_root().pid

    running, pid = _service_state()
    if not running:
        print("Service is not running.")
        pid_path.unlink(missing_ok=True)
        return 0

    print(f"Stopping service (pid {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    if not _wait_for_stopped(pid):
        print("Service did not stop gracefully; sending SIGKILL...", file=_sys.stderr)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass
    print("Service stopped.")
    return 0


def _start_service(config_path: str | None, log_dir: str | None) -> int:
    """Start the daemon and wait until it is ready."""
    import subprocess
    import sys as _sys
    from pathlib import Path

    from knoa_platform.service.core_daemon import resolve_core_log

    running, pid = _service_state()
    if running:
        print(f"Service already running (pid {pid}).")
        print(f"Log: {_service_log_path()}")
        return 0

    # Spawn the dedicated service module directly. This avoids recursively
    # entering the user-facing CLI and keeps restart working even before the
    # renamed `knoa` console script has been reinstalled.
    cmd = [_sys.executable, "-m", "knoa_platform.service", "--daemon"]
    if log_dir:
        cmd += [f"--log-dir={Path(log_dir).expanduser()}"]
    if config_path:
        cmd += ["--config", config_path]

    print(f"Starting {ASSISTANT_NAME} service (daemon)...")
    try:
        subprocess.Popen(cmd)  # daemon double-forks; parent process exits on its own
    except Exception as e:
        print(f"Failed to start service: {e}", file=_sys.stderr)
        return 1

    if not _wait_for_running(None):
        print("ERROR: Service did not become ready in time.", file=_sys.stderr)
        print(f"Check log: {resolve_core_log(log_dir, config_path)}", file=_sys.stderr)
        return 1

    _, new_pid = _service_state()
    print(f"Service started (pid {new_pid}).")
    print(f"Log: {_service_log_path()}")
    return 0


def _restart_service(config_path: str | None, log_dir: str | None) -> int:
    print("--- restarting service ---")
    rc = _stop_service()
    if rc != 0:
        return rc
    print("---")
    return _start_service(config_path, log_dir)
