"""Small fail-fast validators for runtime-owned SQLite schemas."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence


ColumnSpec = tuple[str, str, bool, str | None, int]
ForeignKeySpec = tuple[str, str, str, str, str]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid SQLite identifier")
    return f'"{value}"'


def require_exact_table(
    connection: sqlite3.Connection,
    table: str,
    expected: Sequence[ColumnSpec],
    *,
    label: str,
) -> None:
    rows = connection.execute(
        f"PRAGMA table_info({_identifier(table)})"
    ).fetchall()
    actual = tuple(
        (
            str(row[1]),
            str(row[2]).upper(),
            bool(row[3]),
            None if row[4] is None else str(row[4]),
            int(row[5]),
        )
        for row in rows
    )
    if actual != tuple(expected):
        raise RuntimeError(
            f"{label} schema is incompatible; run an explicit offline migration"
        )


def require_index_columns(
    connection: sqlite3.Connection,
    index: str,
    expected: Sequence[str],
    *,
    label: str,
) -> None:
    rows = connection.execute(
        f"PRAGMA index_info({_identifier(index)})"
    ).fetchall()
    actual = tuple(str(row[2]) for row in rows)
    if actual != tuple(expected):
        raise RuntimeError(
            f"{label} schema is incompatible; run an explicit offline migration"
        )


def require_foreign_keys(
    connection: sqlite3.Connection,
    table: str,
    expected: Sequence[ForeignKeySpec],
    *,
    label: str,
) -> None:
    rows = connection.execute(
        f"PRAGMA foreign_key_list({_identifier(table)})"
    ).fetchall()
    actual = tuple(
        (
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
        )
        for row in rows
    )
    if actual != tuple(expected):
        raise RuntimeError(
            f"{label} schema is incompatible; run an explicit offline migration"
        )
    violation = connection.execute(
        f"PRAGMA foreign_key_check({_identifier(table)})"
    ).fetchone()
    if violation is not None:
        raise RuntimeError(
            f"{label} data violates foreign-key integrity; repair it offline"
        )
