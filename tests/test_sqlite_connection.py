from __future__ import annotations

import sqlite3

import pytest

from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


def test_context_managed_connection_is_closed(tmp_path) -> None:
    database = tmp_path / "assistant.db"

    with connect_sqlite(database) as connection:
        connection.execute("CREATE TABLE example (value TEXT NOT NULL)")

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_wal_is_initialized_without_leaving_connection_open(tmp_path) -> None:
    database = tmp_path / "assistant.db"

    initialize_wal(database)

    with connect_sqlite(database) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
