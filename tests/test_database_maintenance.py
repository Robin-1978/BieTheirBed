from __future__ import annotations

import sqlite3

from knoa_platform.database_maintenance import maintain_sqlite_database


def test_database_maintenance_checkpoints_wal_without_creating_missing_database(
    tmp_path,
) -> None:
    missing = tmp_path / "missing.db"
    maintain_sqlite_database(missing)
    assert not missing.exists()

    database = tmp_path / "data.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records(value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('ok')")

    maintain_sqlite_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM records").fetchall() == [("ok",)]
