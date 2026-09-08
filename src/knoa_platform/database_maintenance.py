"""Conservative online maintenance for Knoa-owned SQLite databases."""

from __future__ import annotations

from pathlib import Path

from knoa_platform.sqlite_connection import connect_sqlite


def maintain_sqlite_database(path: str | Path) -> None:
    """Checkpoint WAL pages and refresh planner statistics without blocking VACUUM."""
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        return
    wal_file = database.with_name(f"{database.name}-wal")
    checkpoint_mode = (
        "TRUNCATE"
        if (wal_file.is_file() and wal_file.stat().st_size > 10 * 1024 * 1024)
        else "PASSIVE"
    )
    with connect_sqlite(
        database,
        row_factory=False,
        busy_timeout_ms=5_000,
    ) as connection:
        connection.execute(f"PRAGMA wal_checkpoint({checkpoint_mode})")
        connection.execute("PRAGMA optimize")


__all__ = ["maintain_sqlite_database"]
