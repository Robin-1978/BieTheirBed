__version__ = "0.1.0"


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        prog="pca",
        description="PC Assistant - A Python computer assistant agent",
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
    return parser


async def async_main(
    config_path: str | None,
    verbose: bool,
    ask: str | None = None,
    json_output: bool = False,
    no_tools: bool = False,
    attach: list[str] | None = None,
) -> int:
    import logging

    from pc_assistant.config import load_config
    from pc_assistant.agent import Agent
    from pc_assistant.ui.chat import ChatUI
    from pc_assistant.logger import get_logger

    cfg = load_config(config_path)

    if verbose:
        logging.getLogger("pc_assistant").setLevel(logging.DEBUG)

    logger = get_logger("main")
    logger.info("PC Assistant starting (config=%s)", config_path or "default")

    providers_needing_key = {"openai", "anthropic"}
    if cfg.llm_provider in providers_needing_key and not cfg.llm_api_key:
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            console.print(
                Panel(
                    f"Provider '{cfg.llm_provider}' requires an API key.\n"
                    "Please set PC_LLM_API_KEY environment variable\n"
                    "or add llm_api_key to your config file.",
                    title="[red]✗ Missing API Key[/red]",
                    border_style="red",
                    expand=False,
                )
            )
        except ImportError:
            print(f"ERROR: Provider '{cfg.llm_provider}' requires an API key.")
            print("Please set PC_LLM_API_KEY environment variable or add llm_api_key to your config file.")
        return 1

    async def agent_confirm_callback(tool_name: str, arguments: dict) -> bool:
        import asyncio

        title = f"Dangerous operation: {tool_name}"
        details = "\n".join(f"  {k}: {v}" for k, v in arguments.items())
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            console.print(
                Panel(
                    details,
                    title=f"[yellow]⚠ {title}[/yellow]",
                    border_style="yellow",
                    expand=False,
                )
            )
        except ImportError:
            print(f"\n⚠ {title}")
            print(details)
        try:
            answer = await asyncio.to_thread(input, "Proceed? (y/n): ")
            return answer.strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    from pc_assistant.service.lifecycle import get_agent_or_client

    agent = await get_agent_or_client(cfg, no_tools=no_tools)
    is_remote = not isinstance(agent, Agent)

    scheduler = None
    channel_manager = None

    if not is_remote:
        scheduler = agent.registry.get("scheduler")
        if scheduler:
            task_count = scheduler.task_count()
            if task_count > 0:
                await scheduler.execute(action="start")
                logger.info("Scheduler started with %d tasks", task_count)

        logger.info("Checking LLM server health at %s", cfg.llm_server_url)
        healthy = await agent.health_check()
        if not healthy:
            try:
                from rich.console import Console
                from rich.panel import Panel

                console = Console()
                console.print(
                    Panel(
                        f"Could not connect to LLM server at:\n  {cfg.llm_server_url}\n\n"
                        "Please ensure the server is running and accessible.\n"
                        "You can change the server URL with:\n"
                        "  --config path/to/config.yaml\n"
                        "  or set PC_LLM_SERVER_URL environment variable.",
                        title="[red]\u2717 LLM Server Unavailable[/red]",
                        border_style="red",
                        expand=False,
                    )
                )
            except ImportError:
                print(f"ERROR: Could not connect to LLM server at {cfg.llm_server_url}")
            return 1

        logger.info("LLM server is healthy")

        if cfg.feishu_enabled:
            from pc_assistant.channels import create_channels_from_config

            channel_manager = create_channels_from_config(cfg)
            if channel_manager.active_channels:
                await channel_manager.start_all(agent)
                logger.info("Channels started: %s", channel_manager.active_channels)
    else:
        logger.info("Connected to service daemon")

    if ask is not None:
        return await _run_benchmark(agent, ask, json_output=json_output, no_tools=no_tools, attach=attach)

    chat_ui = ChatUI(config=cfg)
    chat_ui.set_agent(agent)

    try:
        await chat_ui.run()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down")
        try:
            from rich.console import Console

            Console().print("\n[dim]Interrupted. Goodbye![/dim]")
        except ImportError:
            print("\nInterrupted. Goodbye!")
    finally:
        if is_remote:
            from pc_assistant.service.client import ServiceClient
            if isinstance(agent, ServiceClient):
                await agent.disconnect()
        else:
            if channel_manager:
                await channel_manager.stop_all()
            if scheduler:
                await scheduler.execute(action="stop")

    return 0


async def _run_benchmark(
    agent: "Agent",
    question: str,
    json_output: bool = False,
    no_tools: bool = False,
    attach: list[str] | None = None,
) -> int:
    import json
    import sys
    import time

    if question == "-":
        question = sys.stdin.read().strip()
        if not question:
            print("Error: no input from stdin", file=sys.stderr)
            return 1

    if no_tools:
        if hasattr(agent, "clear_tools"):
            agent.clear_tools()

    attachments = None
    if attach:
        from pc_assistant.model_adapter.types import ImageAttachment

        attachments = [ImageAttachment.from_path(p) for p in attach]

    start_time = time.monotonic()
    tool_call_count = 0
    answer = None
    error_msg = None

    try:
        async for event in agent.run(question, attachments=attachments):
            if event.type == "tool_call" and not event.blocked:
                tool_call_count += 1
            elif event.type == "final_answer":
                answer = event.content
            elif event.type == "error":
                error_msg = event.content
            elif event.type == "iteration_limit":
                error_msg = event.content
            elif event.type == "cancelled":
                error_msg = event.content
    except Exception as e:
        error_msg = str(e)

    elapsed = time.monotonic() - start_time
    status = await agent.get_status()

    metrics = {
        "elapsed_seconds": round(elapsed, 3),
        "prompt_tokens": status["total_prompt_tokens"],
        "completion_tokens": status["total_completion_tokens"],
        "total_tokens": status["total_tokens"],
        "iterations": status["total_iterations"],
        "tool_calls": tool_call_count,
        "model": status["model"],
        "provider": status["provider"],
    }

    if json_output:
        result = {
            "question": question,
            "answer": answer if not error_msg else None,
            "metrics": metrics,
            "error": error_msg,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Question: {question}")
        print(f"Answer: {answer if not error_msg else 'ERROR: ' + error_msg}")
        print("---")
        print(f"Time: {elapsed:.2f}s")
        print(f"Tokens: prompt={metrics['prompt_tokens']}, "
              f"completion={metrics['completion_tokens']}, "
              f"total={metrics['total_tokens']}")
        print(f"Iterations: {metrics['iterations']}")
        print(f"Tool calls: {metrics['tool_calls']}")

    return 0 if not error_msg else 1


async def async_benchmark(
    config_path: str | None,
    verbose: bool,
    benchmark_path: str,
    categories: list[str] | None = None,
    output_path: str | None = None,
) -> int:
    from pc_assistant.benchmark.runner import BenchmarkRunner
    from pc_assistant.config import load_config

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
    from pathlib import Path

    from pc_assistant.benchmark.reporter import Reporter
    from pc_assistant.benchmark.types import BenchmarkResult

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
    import sys
    from pathlib import Path

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
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

    if args.status:
        return _service_status()

    if args.stop:
        return _stop_service()

    if args.restart:
        return _restart_service(config_path, args.log_dir)

    if args.serve:
        from pc_assistant.service.server import run_server, resolve_service_log
        log_path = resolve_service_log(args.log_dir, config_path)
        return run_server(config_path, daemon=args.daemon, log_path=log_path)

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

    from pc_assistant.service.protocol import PID_PATH

    pid: int | None = None
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().split()[0])
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
        if owners:
            return True, owners[0]

    if PID_PATH.exists():
        try:
            PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    return False, None


def _service_port() -> int:
    """The configured service TCP port (0 means TCP disabled)."""
    try:
        from pc_assistant.config import load_config

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
    from pc_assistant.service.protocol import LOG_PATH, PID_PATH

    if PID_PATH.exists():
        try:
            lines = PID_PATH.read_text().splitlines()
            if len(lines) >= 2 and lines[1].strip():
                return lines[1].strip()
        except OSError:
            pass
    return str(LOG_PATH)


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

    from pc_assistant.service.protocol import PID_PATH

    running, pid = _service_state()
    if not running:
        print("Service is not running.")
        PID_PATH.unlink(missing_ok=True)
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
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    print("Service stopped.")
    return 0


def _start_service(config_path: str | None, log_dir: str | None) -> int:
    """Start the daemon and wait until it is ready."""
    import subprocess
    import sys as _sys
    from pathlib import Path

    from pc_assistant.service.server import resolve_service_log

    running, pid = _service_state()
    if running:
        print(f"Service already running (pid {pid}).")
        print(f"Log: {_service_log_path()}")
        return 0

    # Spawn the dedicated service module directly. This avoids recursively
    # entering the user-facing CLI and keeps restart working even before the
    # renamed `pca` console script has been reinstalled.
    cmd = [_sys.executable, "-m", "pc_assistant.service", "--daemon"]
    if log_dir:
        cmd += [f"--log-dir={Path(log_dir).expanduser()}"]
    if config_path:
        cmd += ["--config", config_path]

    print("Starting PC Assistant service (daemon)...")
    try:
        subprocess.Popen(cmd)  # daemon double-forks; parent process exits on its own
    except Exception as e:
        print(f"Failed to start service: {e}", file=_sys.stderr)
        return 1

    if not _wait_for_running(None):
        print("ERROR: Service did not become ready in time.", file=_sys.stderr)
        print(f"Check log: {resolve_service_log(log_dir, config_path)}", file=_sys.stderr)
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
