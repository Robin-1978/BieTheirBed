__version__ = "0.1.0"


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        prog="pc-assistant",
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
        "--serve", action="store_true", default=False, help="Start the background service daemon"
    )
    parser.add_argument(
        "--daemon", action="store_true", default=False, help="Daemonize the service"
    )
    parser.add_argument(
        "--stop", action="store_true", default=False, help="Stop a running service daemon"
    )
    return parser


async def async_main(
    config_path: str | None,
    verbose: bool,
    ask: str | None = None,
    json_output: bool = False,
    no_tools: bool = False,
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

    agent = await get_agent_or_client(cfg)
    is_remote = not isinstance(agent, Agent)

    scheduler = None
    channel_manager = None

    if not is_remote:
        scheduler = agent.registry.get("scheduler")
        if scheduler:
            task_count = len(scheduler._tasks)
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
        return await _run_benchmark(agent, ask, json_output=json_output, no_tools=no_tools)

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
        from pc_assistant.tools.registry import ToolRegistry
        agent._registry = ToolRegistry()

    start_time = time.monotonic()
    tool_call_count = 0
    answer = None
    error_msg = None

    try:
        async for event in agent.run(question):
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
    status = agent.get_status()

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

    config_path = args.config
    if config_path is not None:
        config_path = str(Path(config_path).resolve())

    if args.stop:
        return _stop_service()

    if args.serve:
        from pc_assistant.service.server import run_server
        return asyncio.run(run_server(config_path, daemon=args.daemon))

    try:
        return asyncio.run(async_main(config_path, args.verbose))
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
