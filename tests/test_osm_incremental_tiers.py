from __future__ import annotations

import os
import struct
import subprocess
import time
from pathlib import Path

import pytest

from examples.osm_map_mcp_server import incremental_update
from examples.osm_map_mcp_server.incremental_update import (
    Scope,
    append_deferred_scope,
    deferred_consolidation_due,
    prune_merge_scope,
    read_deferred_scope,
    write_scope,
)
from examples.osm_map_mcp_server.map_service import (
    MapError,
    MapService,
    MapSettings,
    search_document,
)

GUARD_SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE features(
 id INTEGER PRIMARY KEY,
 osm_type TEXT NOT NULL,osm_id INTEGER NOT NULL,feature_type TEXT NOT NULL,
 name TEXT NOT NULL,aliases TEXT NOT NULL,brand TEXT NOT NULL,
 category TEXT NOT NULL,subcategory TEXT NOT NULL,
 admin_level INTEGER NOT NULL,population INTEGER NOT NULL,
 address TEXT NOT NULL,admin_context TEXT NOT NULL,postcode TEXT NOT NULL,
 phone TEXT NOT NULL,website TEXT NOT NULL,opening_hours TEXT NOT NULL,
 lat REAL NOT NULL,lon REAL NOT NULL,min_lat REAL NOT NULL,min_lon REAL NOT NULL,
 max_lat REAL NOT NULL,max_lon REAL NOT NULL,geom BLOB,tags_json TEXT NOT NULL,
 search_text TEXT NOT NULL,
 UNIQUE(osm_type,osm_id,feature_type)
);
CREATE VIRTUAL TABLE features_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon);
CREATE VIRTUAL TABLE feature_fts USING fts5(search_text,tokenize='unicode61 remove_diacritics 2');
"""


def _builder_binary() -> Path:
    binary = Path(incremental_update.__file__).parent / "bin" / "osm_map_builder"
    if not binary.is_file():
        pytest.skip("osm_map_builder binary is not built")
    return binary


def test_write_scope_marks_ancestors_with_tier(tmp_path: Path) -> None:
    scope = Scope(nodes={7}, ways={42}, relations={100, 200, 300})
    path = tmp_path / "scope.txt"
    write_scope(scope, path, frozenset({100, 200}))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "n 7" in lines
    assert "w 42" in lines
    assert "r 100" in lines
    assert "r 200" in lines
    assert "r 300" not in lines
    assert "R 300" in lines


def test_write_scope_without_direct_keeps_legacy_layout(tmp_path: Path) -> None:
    scope = Scope(nodes={7}, ways={42}, relations={300})
    path = tmp_path / "scope.txt"
    write_scope(scope, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["n 7", "w 42", "R 300"]


def test_deferred_scope_roundtrip_dedupes(tmp_path: Path) -> None:
    path = tmp_path / "deferred_scope.txt"
    assert read_deferred_scope(path) == set()
    append_deferred_scope(path, [300, 100])
    append_deferred_scope(path, [100, 200])
    assert read_deferred_scope(path) == {100, 200, 300}
    assert read_deferred_scope(tmp_path / "missing.txt") == set()


def _encode_tags(tags: dict[str, str]) -> bytes:
    output = b""
    for key, value in tags.items():
        key_bytes = key.encode("utf-8")
        value_bytes = value.encode("utf-8")
        output += struct.pack("<II", len(key_bytes), len(value_bytes))
        output += key_bytes + value_bytes
    return output


def _mini_admin_tree(path: Path) -> None:
    """Synthetic object DB: R_big (100 ways) shares w1 with R_small (10 ways).

    Mirrors the production shape: touching node 1 pulls both ancestors
    upward, and untiered downward expansion reselects all 110 ways.
    Shared border segments (w1 in both relations) are intentional.
    """
    connection = __import__("sqlite3").connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE nodes(id INTEGER PRIMARY KEY,lat_e7 INTEGER NOT NULL,
              lon_e7 INTEGER NOT NULL,tags BLOB NOT NULL);
            CREATE TABLE ways(id INTEGER PRIMARY KEY,tags BLOB NOT NULL);
            CREATE TABLE way_nodes(way_id INTEGER NOT NULL,seq INTEGER NOT NULL,
              node_id INTEGER NOT NULL,PRIMARY KEY(way_id,seq)) WITHOUT ROWID;
            CREATE TABLE relations(id INTEGER PRIMARY KEY,tags BLOB NOT NULL);
            CREATE TABLE relation_members(relation_id INTEGER NOT NULL,
              seq INTEGER NOT NULL,member_type INTEGER NOT NULL,
              member_id INTEGER NOT NULL,role TEXT NOT NULL,
              PRIMARY KEY(relation_id,seq)) WITHOUT ROWID;
            CREATE INDEX relation_members_child
              ON relation_members(member_type,member_id,relation_id);
            CREATE INDEX way_nodes_node ON way_nodes(node_id,way_id);
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            [("replication_timestamp", "2026-10-10T00:00:00Z"),
             ("replication_sequence", "7323000")],
        )
        node_id = 0
        way_nodes: list[tuple[int, int, int]] = []
        for way_id in range(1, 111):
            tags = (
                {"highway": "residential", "name": "Fixture Road"}
                if way_id == 1
                else {}
            )
            connection.execute(
                "INSERT INTO ways(id,tags) VALUES(?,?)",
                (way_id, _encode_tags(tags)),
            )
            # Closed square ring so the area assembler can build polygons.
            ring: list[int] = []
            base_lat = 310_000_000 + way_id * 1_000
            base_lon = 1_210_000_000 + way_id * 1_000
            for dx, dy in ((0, 0), (0, 10), (10, 10), (10, 0)):
                node_id += 1
                connection.execute(
                    "INSERT INTO nodes(id,lat_e7,lon_e7,tags) VALUES(?,?,?,?)",
                    (node_id, base_lat + dy, base_lon + dx,
                     _encode_tags({})),
                )
                ring.append(node_id)
            ring.append(ring[0])
            for sequence, ref in enumerate(ring):
                way_nodes.append((way_id, sequence, ref))
        connection.executemany(
            "INSERT INTO way_nodes(way_id,seq,node_id) VALUES(?,?,?)", way_nodes
        )
        admin = {"boundary": "administrative", "name": "Test", "type": "boundary"}
        for relation_id, members, level in (
            (2, [(1, way_id) for way_id in range(1, 101)], "6"),
            (3, [(1, way_id) for way_id in range(1, 11)], "8"),
        ):
            tags = dict(admin)
            tags["admin_level"] = level
            connection.execute(
                "INSERT INTO relations(id,tags) VALUES(?,?)",
                (relation_id, _encode_tags(tags)),
            )
            connection.executemany(
                "INSERT INTO relation_members(relation_id,seq,member_type,member_id,role)"
                " VALUES(?,?,?,?,?)",
                [
                    (relation_id, sequence, member_type, member_id, "")
                    for sequence, (member_type, member_id) in enumerate(members)
                ],
            )
        connection.commit()
    finally:
        connection.close()


def _run_delta_builder(
    tmp_path: Path, name: str, scope_text: str, extra: list[str]
) -> dict[str, object]:
    objects = tmp_path / f"{name}.objects.sqlite"
    _mini_admin_tree(objects)
    scope_file = tmp_path / f"{name}.txt"
    scope_file.write_text(scope_text, encoding="utf-8")
    output = tmp_path / f"{name}.delta.sqlite"
    command = [
        str(_builder_binary()),
        str(objects),
        str(output),
        "--temp-dir",
        str(tmp_path / f"{name}.tmp"),
        "--batch-size",
        "1000",
        "--delta",
        "--scope",
        str(scope_file),
        "--object-database",
        *extra,
    ]
    completed = subprocess.run(
        command, check=False, text=True, capture_output=True, timeout=300
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return {
        "log": completed.stdout,
        "deferred": read_deferred_scope(
            tmp_path / f"{name}.txt.deferred"
        ),
        "incomplete": read_deferred_scope(
            tmp_path / f"{name}.txt.incomplete"
        ),
        "output": output,
    }


def _delta_features(output: Path) -> dict[tuple[str, int, str], str]:
    connection = __import__("sqlite3").connect(f"file:{output}?mode=ro", uri=True)
    try:
        return {
            (str(osm_type), int(osm_id), str(feature_type)): str(name)
            for osm_type, osm_id, feature_type, name in connection.execute(
                "SELECT osm_type,osm_id,feature_type,name FROM features"
            )
        }
    finally:
        connection.close()


def _selection_counts(log: str) -> tuple[int, int, int]:
    for line in log.splitlines():
        if line.startswith("object selection:"):
            parts = line.replace("object selection:", "").split()
            values = [int(part.split("=")[1]) for part in parts]
            return (values[0], values[1], values[2])
    raise AssertionError("missing object selection log line")


def test_tiered_scope_defers_giant_ancestor(tmp_path: Path) -> None:
    result = _run_delta_builder(
        tmp_path,
        "tiered",
        "n 1\nw 1\nR 2\nR 3\n",
        ["--max-ancestor-members", "50", "--max-selection-nodes", "1000000"],
    )
    nodes, ways, relations = _selection_counts(str(result["log"]))
    assert nodes < 150, result["log"]
    assert result["deferred"] == {2}
    assert result["incomplete"] == set()
    features = _delta_features(result["output"])  # type: ignore[arg-type]
    assert ("way", 1, "road") in features
    assert features[("way", 1, "road")] == "Fixture Road"
    assert ("relation", 3, "boundary") in features
    assert ("relation", 2, "boundary") not in features


def test_untiered_scope_keeps_previous_behavior(tmp_path: Path) -> None:
    result = _run_delta_builder(
        tmp_path,
        "flat",
        "n 1\nw 1\nr 2\nr 3\n",
        ["--max-ancestor-members", "50", "--max-selection-nodes", "1000000"],
    )
    nodes, ways, relations = _selection_counts(str(result["log"]))
    assert nodes > 300, result["log"]
    assert result["deferred"] == set()
    assert result["incomplete"] == set()
    features = _delta_features(result["output"])  # type: ignore[arg-type]
    assert ("relation", 2, "boundary") in features
    assert ("relation", 3, "boundary") in features


def test_tiered_output_matches_flat_output_for_tier0(tmp_path: Path) -> None:
    tiered = _run_delta_builder(
        tmp_path,
        "tiered",
        "n 1\nw 1\nR 2\nR 3\n",
        ["--max-ancestor-members", "50", "--max-selection-nodes", "1000000"],
    )
    flat = _run_delta_builder(
        tmp_path,
        "flat",
        "n 1\nw 1\nr 2\nr 3\n",
        ["--max-ancestor-members", "50", "--max-selection-nodes", "1000000"],
    )
    tiered_features = _delta_features(tiered["output"])  # type: ignore[arg-type]
    flat_features = _delta_features(flat["output"])  # type: ignore[arg-type]
    assert tiered_features[("way", 1, "road")] == flat_features[("way", 1, "road")]
    assert (
        tiered_features[("relation", 3, "boundary")]
        == flat_features[("relation", 3, "boundary")]
    )


def test_selection_cap_defers_all_ancestors(tmp_path: Path) -> None:
    result = _run_delta_builder(
        tmp_path,
        "capped",
        "n 1\nw 1\nR 2\nR 3\n",
        ["--max-ancestor-members", "50", "--max-selection-nodes", "20"],
    )
    nodes, ways, relations = _selection_counts(str(result["log"]))
    assert nodes <= 20, result["log"]
    assert result["deferred"] == {2, 3}
    features = _delta_features(result["output"])  # type: ignore[arg-type]
    assert ("way", 1, "road") in features
    assert not any(key[0] == "relation" for key in features)


def test_prune_merge_scope_keeps_live_features() -> None:
    scope = Scope(nodes={7}, ways={42}, relations={100, 200, 300})
    pruned = prune_merge_scope(scope, {200}, {300})
    assert pruned == {200, 300}
    assert scope.relations == {100}
    assert prune_merge_scope(scope, set(), set()) == set()


def _guard_fixture(path: Path, filler_rows: int = 0) -> None:
    sqlite = __import__("sqlite3")
    database = sqlite.connect(path)
    try:
        database.executescript(GUARD_SCHEMA)
        database.executemany(
            "INSERT INTO metadata(key,value) VALUES (?,?)",
            [("schema_version", "1"), ("build_state", "ready")],
        )
        document = search_document("测试镇", "", "", "", "上海市测试区", "place", "town")
        database.execute(
            "INSERT INTO features(id,osm_type,osm_id,feature_type,name,aliases,brand,"
            "category,subcategory,admin_level,population,address,admin_context,"
            "postcode,phone,website,opening_hours,lat,lon,min_lat,min_lon,max_lat,"
            "max_lon,geom,tags_json,search_text)"
            " VALUES(1,'node',1,'place','测试镇','','','place','town',0,0,'',"
            "'上海市测试区','','','','',31.2,121.4,31.2,121.4,31.2,121.4,"
            "NULL,'{}',?)",
            (document,),
        )
        database.execute(
            "INSERT INTO features_rtree VALUES(1,31.2,31.2,121.4,121.4)"
        )
        database.execute(
            "INSERT INTO feature_fts(rowid,search_text) VALUES(1,?)", (document,)
        )
        if filler_rows:
            filler = search_document(
                "填补测试", "", "", "", "上海市测试区", "place", "town"
            )
            database.executemany(
                "INSERT INTO features(id,osm_type,osm_id,feature_type,name,aliases,"
                "brand,category,subcategory,admin_level,population,address,"
                "admin_context,postcode,phone,website,opening_hours,lat,lon,"
                "min_lat,min_lon,max_lat,max_lon,geom,tags_json,search_text)"
                " VALUES(?,'node',?,'place','填补测试','','','place','town',0,0,'',"
                "'上海市测试区','','','','',31.2,121.4,31.2,121.4,31.2,121.4,"
                "NULL,'{}',?)",
                [(1000 + index, 1000 + index, filler) for index in range(filler_rows)],
            )
            database.executemany(
                "INSERT INTO feature_fts(rowid,search_text) VALUES(?,?)",
                [(1000 + index, filler) for index in range(filler_rows)],
            )
        database.commit()
    finally:
        database.close()


def test_search_rows_aborts_past_deadline(tmp_path: Path) -> None:
    path = tmp_path / "guard.sqlite"
    _guard_fixture(path, filler_rows=300_000)
    service = MapService(
        MapSettings(database_path=path, query_timeout_seconds=0.0)
    )
    with pytest.raises(MapError) as caught:
        service.search_places("填补测试", limit=5)
    assert caught.value.code == "search_timeout"


def test_search_rows_succeeds_with_budget(tmp_path: Path) -> None:
    path = tmp_path / "guard.sqlite"
    _guard_fixture(path)
    service = MapService(MapSettings(database_path=path))
    result = service.search_places("测试镇", limit=5)
    assert result["count"] >= 1


def test_nearby_search_aborts_past_deadline(tmp_path: Path) -> None:
    path = tmp_path / "guard.sqlite"
    _guard_fixture(path, filler_rows=300_000)
    service = MapService(
        MapSettings(database_path=path, query_timeout_seconds=0.0)
    )
    with pytest.raises(MapError) as caught:
        service.nearby_search(
            31.2, 121.4, radius_m=1000, query="填补测试", limit=5
        )
    assert caught.value.code == "search_timeout"


def test_route_total_timeout_fires(tmp_path: Path) -> None:
    path = tmp_path / "guard.sqlite"
    _guard_fixture(path)
    service = MapService(
        MapSettings(database_path=path, route_total_timeout_seconds=0.0)
    )
    with pytest.raises(MapError) as caught:
        service.route("测试镇", "测试镇", mode="driving")
    assert caught.value.code == "route_timeout"


def test_deferred_consolidation_off_peak_and_max_age(tmp_path: Path) -> None:
    path = tmp_path / "deferred_scope.txt"
    assert deferred_consolidation_due(
        path, window_start=2, window_end=5, max_age_hours=24.0
    ) is False
    append_deferred_scope(path, [300])
    hour = time.localtime().tm_hour
    in_window = 2 <= hour < 5
    assert (
        deferred_consolidation_due(
            path, window_start=2, window_end=5, max_age_hours=24.0
        )
        is in_window
    )
    assert (
        deferred_consolidation_due(
            path, window_start=2, window_end=5, max_age_hours=0.0
        )
        is True
    )
    old = time.time() - 25 * 3600
    os.utime(path, (old, old))
    assert (
        deferred_consolidation_due(
            path, window_start=9, window_end=10, max_age_hours=24.0
        )
        is True
    )
