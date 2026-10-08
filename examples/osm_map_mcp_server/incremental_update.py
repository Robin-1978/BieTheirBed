#!/usr/bin/env python3
"""Apply OSM replication diffs to the offline map database incrementally."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import time
from typing import Any, Iterable


def _osmium() -> Any:
    try:
        import osmium  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "incremental updates require pyosmium; run this script with /usr/bin/python3"
        ) from exc
    return osmium


@dataclass
class Scope:
    nodes: set[int] = field(default_factory=set)
    ways: set[int] = field(default_factory=set)
    relations: set[int] = field(default_factory=set)

    def counts(self) -> dict[str, int]:
        return {
            "nodes": len(self.nodes),
            "ways": len(self.ways),
            "relations": len(self.relations),
        }


@dataclass(frozen=True)
class PbfMetadata:
    sequence: int
    timestamp: str

    base_url: str


@dataclass
class ChangeSet:
    scope: Scope = field(default_factory=Scope)
    nodes: dict[int, tuple[int, int, bytes] | None] = field(default_factory=dict)
    ways: dict[int, tuple[bytes, tuple[int, ...]] | None] = field(default_factory=dict)
    relations: dict[int, tuple[bytes, tuple[tuple[int, int, str], ...]] | None] = field(
        default_factory=dict
    )


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _archive_changes(change_file: Path, work: Path) -> None:
    if not change_file.is_file():
        return
    archive = work / "applied"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / change_file.name
    _remove(target)
    os.replace(change_file, target)


def _run(command: list[str]) -> None:
    print("run:", " ".join(command), flush=True)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"command failed with exit code {result.returncode}: {command[0]}"
        )


def _run_capture(command: list[str]) -> str:
    print("run:", " ".join(command), flush=True)
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)
    if result.returncode != 0:
        raise SystemExit(
            f"command failed with exit code {result.returncode}: {command[0]}"
        )
    return result.stdout.strip()


def read_pbf_metadata(path: Path) -> PbfMetadata:
    osmium = _osmium()
    reader = osmium.io.Reader(
        osmium.io.File(str(path), "pbf"), osmium.osm.osm_entity_bits.NOTHING
    )
    try:
        header = reader.header()
    finally:
        reader.close()
    sequence_text = header.get("osmosis_replication_sequence_number")
    if not sequence_text:
        raise SystemExit(f"PBF has no replication sequence: {path}")
    return PbfMetadata(
        sequence=int(sequence_text),
        timestamp=header.get("osmosis_replication_timestamp"),
        base_url=header.get("osmosis_replication_base_url"),
    )


def read_replication_metadata(base_url: str, sequence: int) -> PbfMetadata:
    _osmium()
    from osmium.replication.server import ReplicationServer

    state = ReplicationServer(base_url).get_state_info(sequence)
    if state is None or state.sequence != sequence:
        raise SystemExit(f"could not read replication state {sequence}")
    return PbfMetadata(
        sequence=sequence,
        timestamp=state.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        base_url=base_url,
    )


def read_database_metadata(database: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        return dict(connection.execute("SELECT key,value FROM metadata"))
    finally:
        connection.close()


def _encode_tags(tags: Any) -> bytes:
    result = bytearray()
    for tag in tags:
        key = str(tag.k).encode("utf-8")
        value = str(tag.v).encode("utf-8")
        result.extend(struct.pack("<II", len(key), len(value)))
        result.extend(key)
        result.extend(value)
    return bytes(result)


def _decode_tags(value: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    offset = 0
    while offset < len(value):
        if offset + 8 > len(value):
            raise SystemExit("dependency object contains malformed tags")
        key_size, value_size = struct.unpack_from("<II", value, offset)
        offset += 8
        end = offset + key_size + value_size
        if end > len(value):
            raise SystemExit("dependency object contains truncated tags")
        key = value[offset : offset + key_size].decode("utf-8")
        offset += key_size
        result[key] = value[offset : offset + value_size].decode("utf-8")
        offset += value_size
    return result


def read_changes(change_file: Path) -> ChangeSet:
    osmium = _osmium()
    bits = osmium.osm.osm_entity_bits
    changes = ChangeSet()
    for node in osmium.FileProcessor(str(change_file), bits.NODE):
        object_id = int(node.id)
        changes.scope.nodes.add(object_id)
        if node.deleted or not node.location.valid():
            changes.nodes[object_id] = None
        else:
            changes.nodes[object_id] = (
                round(node.location.lat * 10_000_000),
                round(node.location.lon * 10_000_000),
                _encode_tags(node.tags),
            )
    for way in osmium.FileProcessor(str(change_file), bits.WAY):
        object_id = int(way.id)
        changes.scope.ways.add(object_id)
        changes.ways[object_id] = (
            None
            if way.deleted
            else (_encode_tags(way.tags), tuple(int(node.ref) for node in way.nodes))
        )
    member_types = {"n": 0, "w": 1, "r": 2}
    for relation in osmium.FileProcessor(str(change_file), bits.RELATION):
        object_id = int(relation.id)
        changes.scope.relations.add(object_id)
        changes.relations[object_id] = (
            None
            if relation.deleted
            else (
                _encode_tags(relation.tags),
                tuple(
                    (member_types[member.type], int(member.ref), str(member.role))
                    for member in relation.members
                ),
            )
        )
    return changes


def write_scope(scope: Scope, path: Path) -> None:
    with path.open("w", encoding="utf-8") as output:
        for object_id in sorted(scope.nodes):
            output.write(f"n {object_id}\n")
        for object_id in sorted(scope.ways):
            output.write(f"w {object_id}\n")
        for object_id in sorted(scope.relations):
            output.write(f"r {object_id}\n")
        output.flush()
        os.fsync(output.fileno())


def dependency_scope(connection: sqlite3.Connection, direct: Scope) -> Scope:
    connection.execute("DROP TABLE IF EXISTS temp.affected_objects")
    connection.execute(
        "CREATE TEMP TABLE affected_objects(object_type INTEGER NOT NULL,id INTEGER NOT NULL,"
        "PRIMARY KEY(object_type,id)) WITHOUT ROWID"
    )
    connection.executemany(
        "INSERT INTO affected_objects VALUES(0,?)", ((value,) for value in direct.nodes)
    )
    connection.executemany(
        "INSERT INTO affected_objects VALUES(1,?)", ((value,) for value in direct.ways)
    )
    connection.executemany(
        "INSERT INTO affected_objects VALUES(2,?)",
        ((value,) for value in direct.relations),
    )
    connection.execute(
        "INSERT OR IGNORE INTO affected_objects "
        "SELECT 1,way_nodes.way_id FROM affected_objects "
        "JOIN way_nodes INDEXED BY way_nodes_node "
        "ON way_nodes.node_id=affected_objects.id "
        "WHERE affected_objects.object_type=0"
    )
    for _depth in range(32):
        connection.execute(
            "INSERT OR IGNORE INTO affected_objects "
            "SELECT 2,relation_members.relation_id FROM affected_objects "
            "JOIN relation_members INDEXED BY relation_members_child "
            "ON relation_members.member_type=affected_objects.object_type "
            "AND relation_members.member_id=affected_objects.id"
        )
        added = int(connection.execute("SELECT changes()").fetchone()[0])
        if added == 0:
            break
    else:
        raise SystemExit("relation dependency closure exceeded 32 levels")
    scope = Scope()
    for object_type, object_id in connection.execute(
        "SELECT object_type,id FROM affected_objects"
    ):
        ({0: scope.nodes, 1: scope.ways, 2: scope.relations}[int(object_type)]).add(
            int(object_id)
        )
    return scope


def apply_dependency_changes(
    connection: sqlite3.Connection, changes: ChangeSet
) -> None:
    connection.executemany(
        "DELETE FROM nodes WHERE id=?", ((value,) for value in changes.nodes)
    )
    connection.executemany(
        "INSERT INTO nodes(id,lat_e7,lon_e7,tags) VALUES(?,?,?,?)",
        (
            (object_id, value[0], value[1], value[2])
            for object_id, value in changes.nodes.items()
            if value is not None
        ),
    )
    for object_id, value in changes.ways.items():
        connection.execute("DELETE FROM way_nodes WHERE way_id=?", (object_id,))
        connection.execute("DELETE FROM ways WHERE id=?", (object_id,))
        if value is None:
            continue
        tags, node_ids = value
        connection.execute("INSERT INTO ways(id,tags) VALUES(?,?)", (object_id, tags))
        connection.executemany(
            "INSERT INTO way_nodes(way_id,seq,node_id) VALUES(?,?,?)",
            (
                (object_id, sequence, node_id)
                for sequence, node_id in enumerate(node_ids)
            ),
        )
    for object_id, value in changes.relations.items():
        connection.execute(
            "DELETE FROM relation_members WHERE relation_id=?", (object_id,)
        )
        connection.execute("DELETE FROM relations WHERE id=?", (object_id,))
        if value is None:
            continue
        tags, members = value
        connection.execute(
            "INSERT INTO relations(id,tags) VALUES(?,?)", (object_id, tags)
        )
        connection.executemany(
            "INSERT INTO relation_members(relation_id,seq,member_type,member_id,role) "
            "VALUES(?,?,?,?,?)",
            (
                (object_id, sequence, member_type, member_id, role)
                for sequence, (member_type, member_id, role) in enumerate(members)
            ),
        )


def _union_scope(left: Scope, right: Scope) -> Scope:
    return Scope(
        nodes=left.nodes | right.nodes,
        ways=left.ways | right.ways,
        relations=left.relations | right.relations,
    )


def prepare_dependency_update(
    database: Path, changes: ChangeSet
) -> tuple[sqlite3.Connection, Scope]:
    connection = sqlite3.connect(database, timeout=3600)
    connection.execute("PRAGMA busy_timeout=3600000")
    connection.execute("PRAGMA cache_size=-262144")
    connection.execute("PRAGMA mmap_size=1073741824")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("BEGIN IMMEDIATE")
    try:
        old_scope = dependency_scope(connection, changes.scope)
        apply_dependency_changes(connection, changes)
        new_scope = dependency_scope(connection, changes.scope)
        scope = _union_scope(old_scope, new_scope)
        print(
            f"dependency scope direct={changes.scope.counts()} expanded={scope.counts()}",
            flush=True,
        )
        return connection, scope
    except BaseException:
        connection.rollback()
        connection.close()
        raise


def commit_dependency_update(
    connection: sqlite3.Connection, metadata: PbfMetadata
) -> None:
    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (
            ("replication_sequence", str(metadata.sequence)),
            ("replication_timestamp", metadata.timestamp),
            ("replication_base_url", metadata.base_url),
        ),
    )
    connection.commit()
    connection.close()


def stage_dependency_update(
    connection: sqlite3.Connection,
    metadata: PbfMetadata,
    base_sequence: int,
    scope: Scope,
) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS pending_scope("
        "object_type INTEGER NOT NULL,id INTEGER NOT NULL,"
        "PRIMARY KEY(object_type,id)) WITHOUT ROWID"
    )
    connection.execute("DELETE FROM pending_scope")
    connection.executemany(
        "INSERT INTO pending_scope VALUES(0,?)", ((value,) for value in scope.nodes)
    )
    connection.executemany(
        "INSERT INTO pending_scope VALUES(1,?)", ((value,) for value in scope.ways)
    )
    connection.executemany(
        "INSERT INTO pending_scope VALUES(2,?)",
        ((value,) for value in scope.relations),
    )
    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (
            ("replication_sequence", str(metadata.sequence)),
            ("replication_timestamp", metadata.timestamp),
            ("replication_base_url", metadata.base_url),
            ("pending_base_sequence", str(base_sequence)),
            ("pending_target_sequence", str(metadata.sequence)),
        ),
    )
    connection.commit()
    connection.close()


def pending_dependency_update(database: Path) -> tuple[int, int, Scope] | None:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        base_text = metadata.get("pending_base_sequence")
        target_text = metadata.get("pending_target_sequence")
        if base_text is None and target_text is None:
            return None
        if base_text is None or target_text is None:
            raise SystemExit(
                "dependency database has an incomplete pending update journal"
            )
        scope = Scope()
        for object_type, object_id in connection.execute(
            "SELECT object_type,id FROM pending_scope"
        ):
            ({0: scope.nodes, 1: scope.ways, 2: scope.relations}[int(object_type)]).add(
                int(object_id)
            )
        return int(base_text), int(target_text), scope
    finally:
        connection.close()


def clear_pending_dependency_update(database: Path) -> None:
    connection = sqlite3.connect(database, timeout=3600)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM pending_scope")
        connection.execute(
            "DELETE FROM metadata WHERE key IN "
            "('pending_base_sequence','pending_target_sequence')"
        )
        connection.commit()
    finally:
        connection.close()


def dependency_metadata(database: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        return dict(connection.execute("SELECT key,value FROM metadata"))
    finally:
        connection.close()


def ensure_dependency_database(
    args: argparse.Namespace,
    database: Path,
    source: Path,
    expected_sequence: int,
) -> int:
    if not database.is_file():
        build_dir = args.dependency_build_dir.resolve()
        build_dir.mkdir(parents=True, exist_ok=True)
        building = build_dir / f"{database.name}.{os.getpid()}.building"
        _remove(building)
        _run([str(args.dependency_builder), str(source), str(building)])
        staged = database.with_name(f".{database.name}.next")
        _stage_file(building, staged)
        os.replace(staged, database)
        _remove(building)
    metadata = dependency_metadata(database)
    if metadata.get("build_state") != "ready":
        raise SystemExit("OSM dependency database is not ready")
    source_metadata = read_pbf_metadata(source)
    connection = sqlite3.connect(database, timeout=3600)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_scope("
            "object_type INTEGER NOT NULL,id INTEGER NOT NULL,"
            "PRIMARY KEY(object_type,id)) WITHOUT ROWID"
        )
        if not metadata.get("replication_base_url"):
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES('replication_base_url',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (source_metadata.base_url,),
            )
            connection.commit()
    finally:
        connection.close()
    sequence = _metadata_integer(metadata, "replication_sequence")
    return sequence


def repair_dependency_database(
    args: argparse.Namespace,
    database: Path,
    start_sequence: int,
    target_metadata: PbfMetadata,
    work: Path,
) -> None:
    """Finish an object-store commit after the service database already committed."""
    change_file = work / f"changes-{start_sequence}-{target_metadata.sequence}.osc.gz"
    if not change_file.is_file():
        downloaded_sequence = _download_changes(
            args,
            start_sequence,
            target_metadata.sequence,
            target_metadata.base_url,
            change_file,
        )
        if downloaded_sequence != target_metadata.sequence:
            raise SystemExit(
                "dependency recovery downloaded an unexpected replication sequence"
            )
    changes = read_changes(change_file)
    connection = sqlite3.connect(database, timeout=3600)
    connection.execute("PRAGMA busy_timeout=3600000")
    connection.execute("PRAGMA cache_size=-262144")
    connection.execute("PRAGMA mmap_size=1073741824")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("BEGIN IMMEDIATE")
    try:
        apply_dependency_changes(connection, changes)
        connection.execute("DELETE FROM pending_scope")
        connection.execute(
            "DELETE FROM metadata WHERE key IN "
            "('pending_base_sequence','pending_target_sequence')"
        )
        commit_dependency_update(connection, target_metadata)
    except BaseException:
        connection.rollback()
        connection.close()
        raise
    print(
        f"dependency recovery complete sequence={start_sequence}->{target_metadata.sequence}",
        flush=True,
    )
    _archive_changes(change_file, work)


def extract_delta_from_dependencies(
    connection: sqlite3.Connection, scope: Scope, output: Path
) -> None:
    osmium = _osmium()
    _remove(output)
    connection.execute("DROP TABLE IF EXISTS temp.selected_objects")
    connection.execute(
        "CREATE TEMP TABLE selected_objects(object_type INTEGER NOT NULL,id INTEGER NOT NULL,"
        "PRIMARY KEY(object_type,id)) WITHOUT ROWID"
    )
    connection.executemany(
        "INSERT INTO selected_objects VALUES(0,?)", ((value,) for value in scope.nodes)
    )
    connection.executemany(
        "INSERT INTO selected_objects VALUES(1,?)", ((value,) for value in scope.ways)
    )
    connection.executemany(
        "INSERT INTO selected_objects VALUES(2,?)",
        ((value,) for value in scope.relations),
    )
    for _depth in range(32):
        connection.execute(
            "INSERT OR IGNORE INTO selected_objects "
            "SELECT relation_members.member_type,relation_members.member_id "
            "FROM selected_objects CROSS JOIN relation_members "
            "ON selected_objects.object_type=2 "
            "AND relation_members.relation_id=selected_objects.id"
        )
        if int(connection.execute("SELECT changes()").fetchone()[0]) == 0:
            break
    else:
        raise SystemExit("relation reference closure exceeded 32 levels")
    connection.execute(
        "INSERT OR IGNORE INTO selected_objects "
        "SELECT 0,way_nodes.node_id FROM selected_objects CROSS JOIN way_nodes INDEXED BY sqlite_autoindex_way_nodes_1 "
        "ON selected_objects.object_type=1 AND way_nodes.way_id=selected_objects.id"
    )
    with osmium.SimpleWriter(str(output), overwrite=True) as writer:
        for object_id, lat_e7, lon_e7, tags in connection.execute(
            "SELECT nodes.id,nodes.lat_e7,nodes.lon_e7,nodes.tags "
            "FROM selected_objects CROSS JOIN nodes WHERE selected_objects.object_type=0 "
            "AND nodes.id=selected_objects.id ORDER BY selected_objects.id"
        ):
            writer.add_node(
                osmium.osm.mutable.Node(
                    id=int(object_id),
                    location=osmium.osm.Location(
                        float(lon_e7) / 1e7, float(lat_e7) / 1e7
                    ),
                    tags=_decode_tags(bytes(tags)),
                )
            )
        way_rows = connection.execute(
            "SELECT ways.id,ways.tags,way_nodes.node_id FROM selected_objects "
            "CROSS JOIN ways "
            "LEFT JOIN way_nodes INDEXED BY sqlite_autoindex_way_nodes_1 ON way_nodes.way_id=ways.id "
            "WHERE selected_objects.object_type=1 AND ways.id=selected_objects.id "
            "ORDER BY selected_objects.id,way_nodes.seq"
        )
        current_way: int | None = None
        current_tags = b""
        node_ids: list[int] = []
        for object_id, tags, node_id in way_rows:
            object_id = int(object_id)
            if current_way is not None and object_id != current_way:
                writer.add_way(
                    osmium.osm.mutable.Way(
                        id=current_way, nodes=node_ids, tags=_decode_tags(current_tags)
                    )
                )
                node_ids = []
            current_way = object_id
            current_tags = bytes(tags)
            if node_id is not None:
                node_ids.append(int(node_id))
        if current_way is not None:
            writer.add_way(
                osmium.osm.mutable.Way(
                    id=current_way, nodes=node_ids, tags=_decode_tags(current_tags)
                )
            )
        relation_rows = connection.execute(
            "SELECT relations.id,relations.tags,relation_members.member_type,"
            "relation_members.member_id,relation_members.role FROM selected_objects "
            "CROSS JOIN relations "
            "LEFT JOIN relation_members INDEXED BY sqlite_autoindex_relation_members_1 "
            "ON relation_members.relation_id=relations.id "
            "WHERE selected_objects.object_type=2 AND relations.id=selected_objects.id "
            "ORDER BY selected_objects.id,relation_members.seq"
        )
        type_names = {0: "n", 1: "w", 2: "r"}
        current_relation: int | None = None
        current_tags = b""
        members: list[tuple[str, int, str]] = []
        for object_id, tags, member_type, member_id, role in relation_rows:
            object_id = int(object_id)
            if current_relation is not None and object_id != current_relation:
                writer.add_relation(
                    osmium.osm.mutable.Relation(
                        id=current_relation,
                        members=members,
                        tags=_decode_tags(current_tags),
                    )
                )
                members = []
            current_relation = object_id
            current_tags = bytes(tags)
            if member_type is not None:
                members.append(
                    (type_names[int(member_type)], int(member_id), str(role))
                )
        if current_relation is not None:
            writer.add_relation(
                osmium.osm.mutable.Relation(
                    id=current_relation,
                    members=members,
                    tags=_decode_tags(current_tags),
                )
            )


INCREMENTAL_SCHEMA = """
CREATE INDEX IF NOT EXISTS route_edges_way ON route_edges(way_id);
CREATE TRIGGER IF NOT EXISTS features_incremental_insert
AFTER INSERT ON features BEGIN
        INSERT INTO features_rtree
        VALUES(new.id,new.min_lat,new.max_lat,new.min_lon,new.max_lon);
        INSERT INTO feature_fts(rowid,search_text) VALUES(new.id,new.search_text);
END;
CREATE TRIGGER IF NOT EXISTS features_incremental_delete
AFTER DELETE ON features BEGIN
        DELETE FROM features_rtree WHERE id=old.id;
        DELETE FROM feature_fts WHERE rowid=old.id;
END;
CREATE TRIGGER IF NOT EXISTS render_areas_incremental_insert
AFTER INSERT ON render_areas BEGIN
        INSERT INTO render_areas_rtree
        VALUES(new.id,new.min_lat,new.max_lat,new.min_lon,new.max_lon);
END;
CREATE TRIGGER IF NOT EXISTS render_areas_incremental_delete
AFTER DELETE ON render_areas BEGIN
        DELETE FROM render_areas_rtree WHERE id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS route_nodes_incremental_insert
AFTER INSERT ON route_nodes BEGIN
        INSERT INTO route_nodes_rtree VALUES(new.id,new.lat,new.lat,new.lon,new.lon);
END;
CREATE TRIGGER IF NOT EXISTS route_nodes_incremental_update
AFTER UPDATE OF lat,lon ON route_nodes BEGIN
        UPDATE route_nodes_rtree
        SET min_lat=new.lat,max_lat=new.lat,min_lon=new.lon,max_lon=new.lon
        WHERE id=new.id;
END;
CREATE TRIGGER IF NOT EXISTS route_nodes_incremental_delete
AFTER DELETE ON route_nodes BEGIN
        DELETE FROM route_nodes_rtree WHERE id=old.id;
END;
"""


def ensure_incremental_schema(database: Path, temp_dir: Path | None = None) -> None:
    if temp_dir is not None:
        temp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SQLITE_TMPDIR"] = str(temp_dir.resolve())
    connection = sqlite3.connect(database, timeout=3600)
    try:
        indexes = {
            str(row[1]) for row in connection.execute("PRAGMA index_list(route_edges)")
        }
        if "route_edges_way" not in indexes:
            print("migration: building route_edges_way index", flush=True)
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("PRAGMA cache_size=-524288")
            connection.execute("PRAGMA mmap_size=1073741824")
            connection.execute("CREATE INDEX route_edges_way ON route_edges(way_id)")
            connection.commit()
            print("migration: route_edges_way index complete", flush=True)
        connection.executescript(INCREMENTAL_SCHEMA)
        connection.commit()
        mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        if mode.lower() != "wal":
            raise SystemExit(f"could not enable SQLite WAL mode: {mode}")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
    finally:
        connection.close()


def _scope_rows(scope: Scope) -> Iterable[tuple[str, int]]:
    yield from (("node", value) for value in scope.nodes)
    yield from (("way", value) for value in scope.ways)
    yield from (("relation", value) for value in scope.relations)


def _metadata_integer(metadata: dict[str, str], key: str) -> int:
    try:
        return int(metadata[key])
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"database metadata is missing integer {key}") from exc


def _set_metadata(connection: sqlite3.Connection, values: dict[str, str]) -> None:
    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        values.items(),
    )


def merge_delta(
    database: Path,
    delta_database: Path,
    scope: Scope,
    updated_pbf: Path,
    pbf_metadata: PbfMetadata,
) -> dict[str, int]:
    previous_metadata = read_database_metadata(database)
    checkpoint_sequence = previous_metadata.get(
        "source_checkpoint_sequence",
        previous_metadata.get("replication_sequence", ""),
    )
    connection = sqlite3.connect(database, timeout=3600)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=3600000")
    connection.execute("ATTACH DATABASE ? AS delta", (str(delta_database),))
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TEMP TABLE affected(osm_type TEXT NOT NULL,id INTEGER NOT NULL,"
            "PRIMARY KEY(osm_type,id)) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO affected(osm_type,id) VALUES(?,?)", _scope_rows(scope)
        )
        connection.execute(
            "CREATE TEMP TABLE touched_nodes(id INTEGER PRIMARY KEY) WITHOUT ROWID"
        )
        connection.execute(
            "INSERT OR IGNORE INTO touched_nodes "
            "SELECT route_edges.source FROM affected "
            "JOIN route_edges INDEXED BY route_edges_way "
            "ON route_edges.way_id=affected.id WHERE affected.osm_type='way'"
        )
        connection.execute(
            "INSERT OR IGNORE INTO touched_nodes "
            "SELECT route_edges.target FROM affected "
            "JOIN route_edges INDEXED BY route_edges_way "
            "ON route_edges.way_id=affected.id WHERE affected.osm_type='way'"
        )

        old_feature_counts = dict(
            connection.execute(
                "SELECT feature_type,count(*) FROM features JOIN affected "
                "ON affected.osm_type=features.osm_type "
                "AND affected.id=features.osm_id GROUP BY feature_type"
            )
        )
        old_area_count = int(
            connection.execute(
                "SELECT count(*) FROM render_areas JOIN affected "
                "ON affected.osm_type=render_areas.osm_type "
                "AND affected.id=render_areas.osm_id"
            ).fetchone()[0]
        )
        old_edge_count = int(
            connection.execute(
                "SELECT count(*) FROM affected "
                "JOIN route_edges INDEXED BY route_edges_way "
                "ON route_edges.way_id=affected.id WHERE affected.osm_type='way'"
            ).fetchone()[0]
        )
        old_touched_node_count = int(
            connection.execute(
                "SELECT count(*) FROM route_nodes JOIN touched_nodes USING(id)"
            ).fetchone()[0]
        )

        connection.execute(
            "DELETE FROM features WHERE id IN "
            "(SELECT features.id FROM affected CROSS JOIN features "
            "ON affected.osm_type=features.osm_type "
            "AND affected.id=features.osm_id)"
        )
        connection.execute(
            "DELETE FROM render_areas WHERE id IN "
            "(SELECT render_areas.id FROM affected CROSS JOIN render_areas "
            "ON affected.osm_type=render_areas.osm_type "
            "AND affected.id=render_areas.osm_id)"
        )
        connection.execute(
            "DELETE FROM route_edges WHERE way_id IN "
            "(SELECT id FROM affected WHERE osm_type='way')"
        )

        feature_columns = (
            "osm_type,osm_id,feature_type,name,aliases,brand,category,subcategory,"
            "admin_level,population,address,admin_context,postcode,phone,website,"
            "opening_hours,lat,lon,min_lat,min_lon,max_lat,max_lon,geom,tags_json,"
            "search_text"
        )
        connection.execute(
            f"INSERT INTO features({feature_columns}) "
            f"SELECT {feature_columns} FROM delta.features"
        )
        area_columns = (
            "osm_type,osm_id,category,subcategory,min_lat,min_lon,max_lat,max_lon,geom"
        )
        connection.execute(
            f"INSERT INTO render_areas({area_columns}) "
            f"SELECT {area_columns} FROM delta.render_areas"
        )
        edge_columns = (
            "way_id,seq,source,target,length_m,forward_modes,backward_modes,name,"
            "highway,speed_kph,layer,structure"
        )
        connection.execute(
            f"INSERT INTO route_edges({edge_columns}) "
            f"SELECT {edge_columns} FROM delta.route_edges"
        )
        connection.execute(
            "INSERT OR IGNORE INTO touched_nodes SELECT source FROM delta.route_edges"
        )
        connection.execute(
            "INSERT OR IGNORE INTO touched_nodes SELECT target FROM delta.route_edges"
        )
        connection.execute(
            "INSERT OR IGNORE INTO touched_nodes SELECT id FROM delta.route_nodes"
        )
        connection.execute(
            "INSERT INTO route_nodes(id,lat,lon,modes) "
            "SELECT id,lat,lon,modes FROM delta.route_nodes WHERE 1 "
            "ON CONFLICT(id) DO UPDATE SET lat=excluded.lat,lon=excluded.lon"
        )

        connection.execute(
            "CREATE TEMP TABLE raw_node_modes(id INTEGER NOT NULL,modes INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO raw_node_modes SELECT route_edges.source,"
            "route_edges.forward_modes|route_edges.backward_modes "
            "FROM touched_nodes JOIN route_edges INDEXED BY route_edges_source "
            "ON route_edges.source=touched_nodes.id"
        )
        connection.execute(
            "INSERT INTO raw_node_modes SELECT route_edges.target,"
            "route_edges.forward_modes|route_edges.backward_modes "
            "FROM touched_nodes JOIN route_edges INDEXED BY route_edges_target "
            "ON route_edges.target=touched_nodes.id"
        )
        connection.execute(
            "CREATE TEMP TABLE node_modes(id INTEGER PRIMARY KEY,modes INTEGER NOT NULL) "
            "WITHOUT ROWID"
        )
        connection.execute(
            "INSERT INTO node_modes SELECT id,"
            "max(modes&1)+max(modes&2)+max(modes&4) "
            "FROM raw_node_modes GROUP BY id"
        )
        connection.execute(
            "UPDATE route_nodes SET modes=(SELECT modes FROM node_modes "
            "WHERE node_modes.id=route_nodes.id) "
            "WHERE id IN (SELECT id FROM node_modes)"
        )
        connection.execute(
            "DELETE FROM route_nodes WHERE id IN (SELECT id FROM touched_nodes) "
            "AND id NOT IN (SELECT id FROM node_modes)"
        )

        new_feature_counts = dict(
            connection.execute(
                "SELECT feature_type,count(*) FROM delta.features GROUP BY feature_type"
            )
        )
        new_area_count = int(
            connection.execute("SELECT count(*) FROM delta.render_areas").fetchone()[0]
        )
        new_edge_count = int(
            connection.execute("SELECT count(*) FROM delta.route_edges").fetchone()[0]
        )
        new_touched_node_count = int(
            connection.execute(
                "SELECT count(*) FROM route_nodes JOIN touched_nodes USING(id)"
            ).fetchone()[0]
        )

        count_metadata: dict[str, str] = {}
        feature_types = {
            "address",
            "boundary",
            "line",
            "place",
            "poi",
            "road",
        }
        for feature_type in feature_types:
            key = f"{feature_type}_count"
            count_metadata[key] = str(
                _metadata_integer(previous_metadata, key)
                - int(old_feature_counts.get(feature_type, 0))
                + int(new_feature_counts.get(feature_type, 0))
            )
        count_metadata["feature_count"] = str(
            _metadata_integer(previous_metadata, "feature_count")
            - sum(int(value) for value in old_feature_counts.values())
            + sum(int(value) for value in new_feature_counts.values())
        )
        count_metadata["render_area_count"] = str(
            _metadata_integer(previous_metadata, "render_area_count")
            - old_area_count
            + new_area_count
        )
        count_metadata["route_edge_count"] = str(
            _metadata_integer(previous_metadata, "route_edge_count")
            - old_edge_count
            + new_edge_count
        )
        count_metadata["route_node_count"] = str(
            _metadata_integer(previous_metadata, "route_node_count")
            - old_touched_node_count
            + new_touched_node_count
        )

        source_stat = updated_pbf.stat()
        _set_metadata(
            connection,
            {
                **count_metadata,
                "build_state": "ready",
                "build_phase": "incremental_complete",
                "build_completed_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "builder": "libosmium-cpp-delta-v1",
                "replication_sequence": str(pbf_metadata.sequence),
                "replication_timestamp": pbf_metadata.timestamp,
                "replication_base_url": pbf_metadata.base_url,
                "source_name": str(updated_pbf.resolve()),
                "source_size": str(source_stat.st_size),
                "source_mtime_ns": str(source_stat.st_mtime_ns),
                "source_sha256": previous_metadata.get("source_sha256", ""),
                "source_checkpoint_sequence": checkpoint_sequence,
            },
        )
        connection.commit()
        return {
            "old_features": sum(int(value) for value in old_feature_counts.values()),
            "new_features": sum(int(value) for value in new_feature_counts.values()),
            "old_areas": old_area_count,
            "new_areas": new_area_count,
            "old_edges": old_edge_count,
            "new_edges": new_edge_count,
            "old_touched_nodes": old_touched_node_count,
            "new_touched_nodes": new_touched_node_count,
        }
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _stage_file(source: Path, target: Path) -> None:
    _remove(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=16 * 1024 * 1024)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    shutil.copystat(source, target)


def _rotate_source(source: Path, staged: Path) -> None:
    previous = source.with_name(f"{source.name}.previous")
    _remove(previous)
    if source.exists():
        os.replace(source, previous)
    os.replace(staged, source)
    directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _download_changes(
    args: argparse.Namespace,
    start_sequence: int,
    end_sequence: int | None,
    base_url: str,
    change_file: Path,
) -> int:
    _remove(change_file)
    command = [
        str(args.change_downloader),
        "--start-id",
        str(start_sequence),
        "--outfile",
        str(change_file),
        "--size",
        str(args.diff_batch_mb),
        "--socket-timeout",
        str(args.socket_timeout),
        "--server",
        args.server or base_url,
    ]
    if end_sequence is not None:
        command.extend(("--end-id", str(end_sequence)))
    output = _run_capture(command)
    try:
        return int(output.splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise SystemExit(
            "change downloader did not return a replication sequence"
        ) from exc


def _prepare_updated_pbf(
    args: argparse.Namespace,
    old_pbf: Path,
    target_sequence: int,
    work: Path,
) -> Path:
    updated = work / "updated.osm.pbf"
    _remove(updated)
    command = [
        str(args.pbf_updater),
        str(old_pbf),
        "--outfile",
        str(updated),
        "--tmpdir",
        str(work),
        "--end-id",
        str(target_sequence),
        "--size",
        str(args.diff_batch_mb),
        "--socket-timeout",
        str(args.socket_timeout),
    ]
    if args.server:
        command.extend(("--server", args.server))
    _run(command)
    metadata = read_pbf_metadata(updated)
    if metadata.sequence != target_sequence:
        raise SystemExit(
            f"updated PBF sequence {metadata.sequence} does not match {target_sequence}"
        )
    return updated


def update(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    database = args.database.resolve()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if not source.is_file() or not database.is_file():
        raise SystemExit("both source PBF and database must exist")

    lock_path = work / "osm-map-incremental-update.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(
                "another incremental map update is already running"
            ) from None
        lock.write(f"pid={os.getpid()} started={time.time()}\n")
        lock.flush()
        return _update_locked(args, source, database, work)


def _build_and_merge_direct(
    args: argparse.Namespace,
    source: Path,
    database: Path,
    dependency_database: Path,
    work: Path,
    base_sequence: int,
    target_metadata: PbfMetadata,
    scope: Scope,
) -> dict[str, int]:
    scope_file = work / f"scope-{base_sequence}-{target_metadata.sequence}.txt"
    delta_database = work / f"delta-{base_sequence}-{target_metadata.sequence}.sqlite"
    write_scope(scope, scope_file)
    _remove(delta_database)
    _run(
        [
            str(args.builder),
            str(dependency_database),
            str(delta_database),
            "--temp-dir",
            str(args.index_temp_dir),
            "--batch-size",
            str(args.build_batch_size),
            "--delta",
            "--scope",
            str(scope_file),
            "--object-database",
        ]
    )
    ensure_incremental_schema(database, args.index_temp_dir.resolve())
    merged = merge_delta(database, delta_database, scope, source, target_metadata)
    clear_pending_dependency_update(dependency_database)
    _remove(delta_database)
    _remove(scope_file)
    return merged


def _update_locked(
    args: argparse.Namespace, source: Path, database: Path, work: Path
) -> int:
    database_metadata = read_database_metadata(database)
    database_sequence = _metadata_integer(database_metadata, "replication_sequence")
    checkpoint_metadata = read_pbf_metadata(source)

    dependency_database = (
        args.dependency_database.resolve()
        if args.dependency_database is not None
        else database.with_name(f"{database.stem}-osm-objects.sqlite")
    )
    dependency_sequence = ensure_dependency_database(
        args, dependency_database, source, database_sequence
    )
    dependency_values = dependency_metadata(dependency_database)
    base_url = (
        args.server
        or dependency_values.get("replication_base_url")
        or database_metadata.get("replication_base_url")
        or checkpoint_metadata.base_url
    )
    if not base_url:
        raise SystemExit("replication base URL is missing")

    if dependency_sequence < database_sequence:
        target_metadata = PbfMetadata(
            sequence=database_sequence,
            timestamp=database_metadata.get("replication_timestamp", ""),
            base_url=base_url,
        )
        repair_dependency_database(
            args,
            dependency_database,
            dependency_sequence,
            target_metadata,
            work,
        )
        dependency_sequence = database_sequence
        dependency_values = dependency_metadata(dependency_database)

    pending = pending_dependency_update(dependency_database)
    if dependency_sequence > database_sequence:
        if pending is None:
            raise SystemExit(
                "object database is ahead of the map without a pending update journal"
            )
        pending_base, pending_target, scope = pending
        if pending_base != database_sequence or pending_target != dependency_sequence:
            raise SystemExit("pending update journal does not match database sequences")
        target_metadata = PbfMetadata(
            sequence=dependency_sequence,
            timestamp=dependency_values.get("replication_timestamp", ""),
            base_url=base_url,
        )
        merged = _build_and_merge_direct(
            args,
            source,
            database,
            dependency_database,
            work,
            database_sequence,
            target_metadata,
            scope,
        )
        change_file = work / f"changes-{database_sequence}-{dependency_sequence}.osc.gz"
        _archive_changes(change_file, work)
        print(
            f"incremental recovery complete sequence={database_sequence}->{dependency_sequence} "
            f"scope={scope.counts()} merged={merged}",
            flush=True,
        )
        return 0
    if dependency_sequence != database_sequence:
        raise SystemExit("object and map database sequences cannot be reconciled")
    if pending is not None:
        pending_base, pending_target, _scope = pending
        if pending_target != database_sequence or pending_base > pending_target:
            raise SystemExit("stale pending update journal is inconsistent")
        clear_pending_dependency_update(dependency_database)

    if checkpoint_metadata.sequence > dependency_sequence:
        target_sequence = checkpoint_metadata.sequence
        change_file = work / f"changes-{database_sequence}-{target_sequence}.osc.gz"
        if not change_file.is_file():
            downloaded_sequence = _download_changes(
                args,
                database_sequence,
                target_sequence,
                base_url,
                change_file,
            )
            if downloaded_sequence != target_sequence:
                raise SystemExit("change downloader returned an unexpected sequence")
        target_metadata = checkpoint_metadata
    else:
        change_probe = work / "changes.osc.gz"
        target_sequence = _download_changes(
            args,
            database_sequence,
            None,
            base_url,
            change_probe,
        )
        if target_sequence == database_sequence:
            _remove(change_probe)
            print("map is already current", flush=True)
            return 0
        change_file = work / f"changes-{database_sequence}-{target_sequence}.osc.gz"
        _remove(change_file)
        os.replace(change_probe, change_file)
        target_metadata = read_replication_metadata(base_url, target_sequence)

    changes = read_changes(change_file)
    dependency_connection, scope = prepare_dependency_update(
        dependency_database, changes
    )
    scope_file = work / f"scope-{database_sequence}-{target_sequence}.txt"
    try:
        write_scope(scope, scope_file)
        stage_dependency_update(
            dependency_connection,
            target_metadata,
            database_sequence,
            scope,
        )
    except BaseException:
        dependency_connection.rollback()
        dependency_connection.close()
        raise

    merged = _build_and_merge_direct(
        args,
        source,
        database,
        dependency_database,
        work,
        database_sequence,
        target_metadata,
        scope,
    )

    print(
        f"incremental update complete sequence={database_sequence}->{target_sequence} "
        f"scope={scope.counts()} merged={merged}",
        flush=True,
    )
    _archive_changes(change_file, work)
    return 0


def _parser() -> argparse.ArgumentParser:
    package_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--dependency-database", type=Path)
    parser.add_argument(
        "--dependency-build-dir",
        type=Path,
        default=Path("/disk/dev/osm-map-build"),
    )
    parser.add_argument(
        "--work-dir", type=Path, default=Path("/disk/dev/osm-map-incremental")
    )
    parser.add_argument(
        "--index-temp-dir",
        type=Path,
        default=Path("/disk/dev/osm-map-incremental/sqlite-tmp"),
    )
    parser.add_argument(
        "--change-downloader",
        type=Path,
        default=Path("~/.local/bin/pyosmium-get-changes").expanduser(),
    )
    parser.add_argument(
        "--pbf-updater",
        type=Path,
        default=Path("~/.local/bin/pyosmium-up-to-date").expanduser(),
    )
    parser.add_argument(
        "--builder", type=Path, default=package_root / "bin" / "osm_map_builder"
    )
    parser.add_argument(
        "--dependency-builder",
        type=Path,
        default=package_root / "bin" / "osm_dependency_builder",
    )
    parser.add_argument("--server", default="")
    parser.add_argument("--diff-batch-mb", type=int, default=256)
    parser.add_argument("--socket-timeout", type=int, default=120)
    parser.add_argument("--build-batch-size", type=int, default=50_000)
    return parser


if __name__ == "__main__":
    arguments = _parser().parse_args()
    arguments.diff_batch_mb = max(1, min(arguments.diff_batch_mb, 1024))
    arguments.socket_timeout = max(10, min(arguments.socket_timeout, 600))
    arguments.build_batch_size = max(1_000, min(arguments.build_batch_size, 500_000))
    raise SystemExit(update(arguments))
