"""Conservative online maintenance for Knoa-owned SQLite databases."""

from __future__ import annotations

from pathlib import Path

from knoa_platform.sqlite_connection import connect_sqlite


def maintain_sqlite_database(path: str | Path) -> None:
    """Checkpoint WAL pages and refresh planner statistics without blocking VACUUM."""
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        return
    with connect_sqlite(
        database,
        row_factory=False,
        busy_timeout_ms=5_000,
    ) as connection:
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        connection.execute("PRAGMA optimize")


__all__ = ["maintain_sqlite_database"]
