"""Canonical owner identity and one-time principal data convergence."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from knoa_platform.runtime import RuntimePaths


def legacy_feishu_principal(open_id: str) -> str:
    """Return the pre-canonical Feishu-derived principal for migration only."""
    normalized = open_id.strip()
    if not normalized:
        return ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"personal:feishu:{digest}"


def discover_owner_aliases(
    paths: RuntimePaths,
    configured: tuple[str, ...],
) -> tuple[str, ...]:
    aliases = {value.strip() for value in configured if value.strip()}
    try:
        open_id = (paths.data / "feishu_open_id").read_text(encoding="utf-8").strip()
    except OSError:
        open_id = ""
    legacy = legacy_feishu_principal(open_id)
    if legacy:
        aliases.add(legacy)
    return tuple(sorted(aliases))


def converge_owner_principals(
    paths: RuntimePaths,
    target: str,
    aliases: tuple[str, ...],
) -> None:
    """Atomically move persisted owner data from old aliases to one principal."""
    normalized_target = target.strip()
    sources = tuple(
        sorted(
            {
                alias.strip()
                for alias in aliases
                if alias.strip() and alias.strip() != normalized_target
            }
        )
    )
    if not normalized_target or not sources:
        return
    _converge_database(paths.data / "assistant.db", normalized_target, sources)
    _converge_database(paths.data / "gateway.db", normalized_target, sources)


def _converge_database(path: Path, target: str, sources: tuple[str, ...]) -> None:
    if not path.is_file():
        return
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    placeholders = ",".join("?" for _ in sources)
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "memories" in tables:
            _merge_memories(connection, target, sources, placeholders)
        if "runtime_active_sessions" in tables:
            _merge_active_session(connection, target, sources, placeholders)
        for table in sorted(tables):
            if table in {"memories", "runtime_active_sessions"}:
                continue
            columns = {
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            if "principal_id" not in columns:
                continue
            connection.execute(
                f'UPDATE "{table}" SET principal_id = ? '
                f"WHERE principal_id IN ({placeholders})",
                (target, *sources),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _merge_memories(
    connection: sqlite3.Connection,
    target: str,
    sources: tuple[str, ...],
    placeholders: str,
) -> None:
    rows = connection.execute(
        "SELECT * FROM memories "
        f"WHERE principal_id IN ({placeholders}) "
        "ORDER BY updated_at ASC",
        sources,
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT INTO memories (
                principal_id, key, value, category, importance, confidence,
                source, created_at, updated_at, last_used_at, access_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(principal_id, key) DO UPDATE SET
                value = excluded.value,
                category = excluded.category,
                importance = excluded.importance,
                confidence = excluded.confidence,
                source = excluded.source,
                updated_at = excluded.updated_at,
                last_used_at = excluded.last_used_at,
                access_count = excluded.access_count
            WHERE excluded.updated_at >= memories.updated_at
            """,
            (
                target,
                row["key"],
                row["value"],
                row["category"],
                row["importance"],
                row["confidence"],
                row["source"],
                row["created_at"],
                row["updated_at"],
                row["last_used_at"],
                row["access_count"],
            ),
        )
    connection.execute(
        f"DELETE FROM memories WHERE principal_id IN ({placeholders})",
        sources,
    )


def _merge_active_session(
    connection: sqlite3.Connection,
    target: str,
    sources: tuple[str, ...],
    placeholders: str,
) -> None:
    row = connection.execute(
        "SELECT session_handle, updated_at FROM runtime_active_sessions "
        f"WHERE principal_id = ? OR principal_id IN ({placeholders}) "
        "ORDER BY updated_at DESC LIMIT 1",
        (target, *sources),
    ).fetchone()
    connection.execute(
        "DELETE FROM runtime_active_sessions "
        f"WHERE principal_id = ? OR principal_id IN ({placeholders})",
        (target, *sources),
    )
    if row is not None:
        connection.execute(
            "INSERT INTO runtime_active_sessions "
            "(principal_id, session_handle, updated_at) VALUES (?, ?, ?)",
            (target, row["session_handle"], row["updated_at"]),
        )
