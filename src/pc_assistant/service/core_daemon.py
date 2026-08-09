"""Process lifecycle for the forward-only Core service."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from pc_assistant.branding import ASSISTANT_NAME

from pc_assistant.agent_runtime.composition import (
    CoreRuntimeComposition,
    build_core_runtime,
)
from pc_assistant.config import AppConfig, load_config
from pc_assistant.runtime import RuntimePaths


logger = logging.getLogger(__name__)


def _prepare_private_file(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    path.chmod(0o600)


class CoreDaemon:
    def __init__(self, config: AppConfig, *, log_path: Path) -> None:
        self._config = config
        self._log_path = log_path
        self._composition: CoreRuntimeComposition | None = None
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._composition is not None:
            raise RuntimeError("Core daemon is already started")
        composition = build_core_runtime(self._config)
        try:
            await composition.extensions.start()
            await composition.host.start()
            self._write_pid(composition.paths.pid)
        except BaseException:
            await composition.host.stop()
            await composition.extensions.stop()
            raise
        self._composition = composition
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Core service ready (pid %d)", os.getpid())

    async def stop(self) -> None:
        cleanup, self._cleanup_task = self._cleanup_task, None
        if cleanup is not None:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
        composition, self._composition = self._composition, None
        if composition is None:
            return
        await composition.host.stop()
        await composition.extensions.stop()
        composition.paths.pid.unlink(missing_ok=True)
        logger.info("Core service stopped")

    async def serve_forever(self) -> None:
        if self._composition is None:
            raise RuntimeError("Core daemon is not started")
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()
        await self.stop()

    async def _cleanup_loop(self) -> None:
        interval = max(10, self._config.attachment_cleanup_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            composition = self._composition
            if composition is not None:
                await asyncio.to_thread(composition.artifacts.cleanup_expired)

    def _write_pid(self, path: Path) -> None:
        _prepare_private_file(path)
        path.write_text(
            f"{os.getpid()}\n{self._log_path}\n",
            encoding="utf-8",
        )


def resolve_core_log(
    log_dir: str | None,
    config_path: str | None = None,
) -> Path:
    if log_dir:
        return Path(log_dir).expanduser().resolve() / "service.log"
    config = load_config(config_path) if config_path else load_config()
    return RuntimePaths.from_root(config.runtime_root).logs / "service.log"


def run_core_server(
    config_path: str | None = None,
    *,
    daemon: bool = False,
    log_path: Path | None = None,
) -> int:
    from pc_assistant.network_tls import ensure_default_ca_bundle

    ensure_default_ca_bundle()
    resolved_log = log_path or resolve_core_log(None, config_path)
    if daemon:
        daemonize(resolved_log)
    asyncio.run(_serve(config_path, daemon, resolved_log))
    return 0


async def _serve(
    config_path: str | None,
    daemon: bool,
    log_path: Path,
) -> None:
    config = load_config(config_path) if config_path else load_config()
    _prepare_private_file(log_path)
    handlers: list[logging.Handler] = [
        logging.FileHandler(str(log_path), mode="a"),
    ]
    if not daemon:
        handlers.insert(0, logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    service = CoreDaemon(config, log_path=log_path)
    await service.start()
    await service.serve_forever()


def daemonize(log_path: Path) -> None:
    _prepare_private_file(log_path)
    if os.fork() > 0:
        raise SystemExit(0)
    os.setsid()
    if os.fork() > 0:
        raise SystemExit(0)
    sys.stdin.close()
    sys.stdout = open(str(log_path), "a", encoding="utf-8")  # noqa: SIM115
    sys.stderr = sys.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{ASSISTANT_NAME} Core Service")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--log-dir", type=str, default=None)
    args = parser.parse_args(argv)
    log_path = resolve_core_log(args.log_dir, args.config)
    return run_core_server(
        args.config,
        daemon=args.daemon,
        log_path=log_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
