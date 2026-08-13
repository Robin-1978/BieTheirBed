from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "lineNo": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        base = f"{ts} [{record.levelname:<8}] {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + self.formatException(record.exc_info)
        return base


_loggers: dict[str, logging.Logger] = {}
_initialized = False


def _ensure_log_dir(log_file: str) -> Path:
    log_path = Path(log_file)
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    log_path.chmod(0o600)
    return log_path


def _setup_root_logger() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger("knoa_platform")
    root.setLevel(logging.DEBUG)

    try:
        from knoa_platform.config import load_config

        cfg = load_config()
        from knoa_platform.runtime import RuntimePaths

        log_file = str(RuntimePaths.from_root(cfg.runtime_root).resolve(cfg.log_file))
    except Exception:
        log_file = "logs/knoa_platform.json"

    log_path = _ensure_log_dir(log_file)

    file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    try:
        from rich.logging import RichHandler

        console_handler = RichHandler(
            level=logging.INFO,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
    except ImportError:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(_HumanFormatter())

    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    _setup_root_logger()
    full_name = f"knoa_platform.{name}" if not name.startswith("knoa_platform.") else name
    if full_name in _loggers:
        return _loggers[full_name]
    logger = logging.getLogger(full_name)
    _loggers[full_name] = logger
    return logger
