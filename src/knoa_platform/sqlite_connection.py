"""Small SQLite connection helpers with deterministic resource ownership."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType

from knoa_platform.private_files import restrict_private_file


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back a context-managed connection, then always close it."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def connect_sqlite(
    path: str | Path,
    *,
    timeout: float = 5.0,
    row_factory: bool = True,
    foreign_keys: bool = False,
    busy_timeout_ms: int | None = None,
) -> sqlite3.Connection:
    """Open a connection whose ``with`` block also owns closing it."""

    connection = sqlite3.connect(
        path,
        timeout=timeout,
        factory=ClosingConnection,
    )
    if row_factory:
        connection.row_factory = sqlite3.Row
    if busy_timeout_ms is not None:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_wal(path: str | Path, *, timeout: float = 5.0) -> None:
    """Enable persistent WAL mode once during repository initialization."""

    database = Path(path)
    with connect_sqlite(database, timeout=timeout, row_factory=False) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    restrict_private_file(database)
