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
    return parser


async def async_main(config_path: str | None, verbose: bool) -> int:
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

    def agent_confirm_callback(tool_name: str, arguments: dict) -> bool:
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
            answer = input("Proceed? (y/n): ").strip().lower()
            return answer in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    agent = Agent(config=cfg, confirm_callback=agent_confirm_callback)

    # Auto-start scheduler if there are tasks
    scheduler = agent.registry.get("scheduler")
    if scheduler:
        task_count = len(scheduler._tasks)
        if task_count > 0:
            await scheduler.execute(action="start")
            logger.info("Scheduler started with %d tasks", task_count)
        else:
            logger.info("No scheduled tasks to run")

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
                    title="[red]✗ LLM Server Unavailable[/red]",
                    border_style="red",
                    expand=False,
                )
            )
        except ImportError:
            print(f"ERROR: Could not connect to LLM server at {cfg.llm_server_url}")
            print("Please ensure the server is running and accessible.")
        return 1

    logger.info("LLM server is healthy")

    chat_ui = ChatUI(config=cfg)
    chat_ui._agent = agent

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
        # Stop scheduler on exit
        if scheduler:
            await scheduler.execute(action="stop")

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

    try:
        return asyncio.run(async_main(config_path, args.verbose))
    except KeyboardInterrupt:
        return 130
