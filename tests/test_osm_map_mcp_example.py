from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
import sqlite3
import struct
import sys

import pytest
import yaml

from examples.osm_map_mcp_server import update_map
from examples.osm_map_mcp_server.map_service import (
    MapService,
    MapSettings,
    convert_coordinate,
    search_document,
)
from knoa_platform.extensions.mcp import StdioMCPClient
from knoa_platform.extensions.models import MCPServerConfig


FEATURE_SCHEMA = """
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
CREATE TABLE route_nodes(id INTEGER PRIMARY KEY,lat REAL NOT NULL,lon REAL NOT NULL,modes INTEGER NOT NULL);
CREATE TABLE route_edges(
 way_id INTEGER NOT NULL,seq INTEGER NOT NULL,source INTEGER NOT NULL,target INTEGER NOT NULL,
 length_m REAL NOT NULL,forward_modes INTEGER NOT NULL,backward_modes INTEGER NOT NULL,
 name TEXT NOT NULL,highway TEXT NOT NULL,speed_kph REAL NOT NULL,
 PRIMARY KEY(way_id,seq)
) WITHOUT ROWID;
CREATE INDEX route_edges_source ON route_edges(source);
CREATE INDEX route_edges_target ON route_edges(target);
CREATE VIRTUAL TABLE route_nodes_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon);
"""


def _point_wkb(longitude: float, latitude: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, longitude, latitude)


def _line_wkb(points: list[tuple[float, float]]) -> bytes:
    return struct.pack("<BII", 1, 2, len(points)) + b"".join(
        struct.pack("<dd", longitude, latitude) for longitude, latitude in points
    )


def _polygon_wkb(points: list[tuple[float, float]]) -> bytes:
    return struct.pack("<BIII", 1, 3, 1, len(points)) + b"".join(
        struct.pack("<dd", longitude, latitude) for longitude, latitude in points
    )


def _feature(
    feature_id: int,
    osm_type: str,
    osm_id: int,
    feature_type: str,
    name: str,
    category: str,
    subcategory: str,
    latitude: float,
    longitude: float,
    *,
    geometry: bytes | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    admin_level: int = 0,
    address: str = "",
    brand: str = "",
) -> tuple:
    min_lat, min_lon, max_lat, max_lon = bounds or (
        latitude,
        longitude,
        latitude,
        longitude,
    )
    document = search_document(name, brand, address, category, subcategory)
    return (
        feature_id,
        osm_type,
        osm_id,
        feature_type,
        name,
        "",
        brand,
        category,
        subcategory,
        admin_level,
        0,
        address,
        "上海市测试区",
        "",
        "",
        "",
        "",
        latitude,
        longitude,
        min_lat,
        min_lon,
        max_lat,
        max_lon,
        geometry,
        "{}",
        document,
    )


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "map.sqlite"
    database = sqlite3.connect(path)
    database.executescript(FEATURE_SCHEMA)
    metadata = {
        "schema_version": "1",
        "build_state": "ready",
        "source_name": "fixture.osm.pbf",
        "source_sha256": "abc",
        "replication_timestamp": "2026-09-30T00:00:00Z",
        "replication_sequence": "42",
        "bounds": json.dumps(
            {
                "min_latitude": 31.19,
                "min_longitude": 121.39,
                "max_latitude": 31.23,
                "max_longitude": 121.44,
            }
        ),
        "feature_count": "5",
        "route_node_count": "3",
        "route_edge_count": "2",
    }
    database.executemany(
        "INSERT INTO metadata(key,value) VALUES (?,?)", metadata.items()
    )
    boundary = [
        (121.39, 31.19),
        (121.44, 31.19),
        (121.44, 31.23),
        (121.39, 31.23),
        (121.39, 31.19),
    ]
    road = [(121.400, 31.200), (121.401, 31.201), (121.402, 31.202)]
    rows = [
        _feature(1, "node", 1, "place", "上海测试镇", "place", "town", 31.200, 121.400),
        _feature(
            2,
            "node",
            2,
            "poi",
            "东方咖啡",
            "amenity",
            "cafe",
            31.201,
            121.401,
            brand="测试品牌",
        ),
        _feature(
            3,
            "node",
            3,
            "address",
            "测试大厦",
            "address",
            "house",
            31.202,
            121.402,
            address="测试路1号",
        ),
        _feature(
            4,
            "way",
            100,
            "road",
            "测试路",
            "highway",
            "residential",
            31.201,
            121.401,
            geometry=_line_wkb(road),
            bounds=(31.200, 121.400, 31.202, 121.402),
        ),
        _feature(
            5,
            "way",
            200,
            "boundary",
            "上海测试区",
            "boundary",
            "admin_level_10",
            31.210,
            121.415,
            geometry=_polygon_wkb(boundary),
            bounds=(31.19, 121.39, 31.23, 121.44),
            admin_level=10,
        ),
    ]
    database.executemany(
        "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    database.executemany(
        "INSERT INTO features_rtree VALUES (?,?,?,?,?)",
        [(row[0], row[19], row[21], row[20], row[22]) for row in rows],
    )
    database.executemany(
        "INSERT INTO feature_fts(rowid,search_text) VALUES (?,?)",
        [(row[0], row[25]) for row in rows],
    )
    nodes = [
        (100, 31.200, 121.400, 7),
        (101, 31.201, 121.401, 7),
        (102, 31.202, 121.402, 7),
    ]
    database.executemany("INSERT INTO route_nodes VALUES (?,?,?,?)", nodes)
    database.executemany(
        "INSERT INTO route_nodes_rtree VALUES (?,?,?,?,?)",
        [(node_id, lat, lat, lon, lon) for node_id, lat, lon, _modes in nodes],
    )
    database.executemany(
        "INSERT INTO route_edges VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (100, 0, 100, 101, 146.0, 7, 3, "测试路", "residential", 30.0),
            (100, 1, 101, 102, 146.0, 7, 3, "测试路", "residential", 30.0),
        ],
    )
    database.commit()
    database.close()
    return path


def test_offline_map_queries_and_routes(tmp_path: Path) -> None:
    service = MapService(MapSettings(database_path=_database(tmp_path)))

    search = service.search_places("东方咖啡")
    assert search["results"][0]["place_id"] == "osm:node:2:poi"

    nearby = service.nearby_search(
        31.201,
        121.401,
        radius_m=500,
        categories=("amenity:cafe",),
    )
    assert [item["name"] for item in nearby["results"]] == ["东方咖啡"]

    reverse = service.reverse_geocode(31.201, 121.401)
    assert reverse["administrative_areas"][0]["name"] == "上海测试区"
    assert reverse["nearby"][0]["distance_m"] == 0

    route = service.route(
        "31.200,121.400",
        "31.202,121.402",
        mode="walking",
    )
    assert route["distance_m"] == 292
    assert route["steps"] == [
        {
            "instruction": "沿测试路行进",
            "name": "测试路",
            "highway": "residential",
            "distance_m": 292,
        }
    ]

    with pytest.raises(Exception, match="not connected"):
        service.route("31.202,121.402", "31.200,121.400", mode="driving")


def test_coordinate_conversion_round_trip() -> None:
    gcj = convert_coordinate(31.2304, 121.4737, "wgs84", "gcj02")
    restored = convert_coordinate(gcj[0], gcj[1], "gcj02", "wgs84")
    assert restored == pytest.approx((31.2304, 121.4737), abs=1e-6)


def test_manifest_matches_read_only_tool_inventory() -> None:
    package = Path(__file__).resolve().parents[1] / "examples/osm_map_mcp_server"
    manifest = yaml.safe_load((package / "mcp.yaml").read_text(encoding="utf-8"))
    expected = {
        "map.search_places",
        "map.reverse_geocode",
        "map.nearby_search",
        "map.get_place",
        "map.route",
        "map.distance",
        "map.convert_coordinates",
        "map.dataset_info",
    }
    assert set(manifest["tools"]) == expected
    assert all(policy["effect"] == "read_only" for policy in manifest["tools"].values())


@pytest.mark.asyncio
async def test_stdio_mcp_inventory_and_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    monkeypatch.setenv("OSM_MAP_DB", str(database))
    repository = Path(__file__).resolve().parents[1]
    config = MCPServerConfig.model_validate(
        {
            "enabled": True,
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "examples.osm_map_mcp_server.server"],
            "working_directory": str(repository),
            "inherit_env": ["OSM_MAP_DB"],
            "timeout_seconds": 15,
        }
    )
    client = StdioMCPClient(config)
    try:
        await client.start()
        tools = await client.list_tools()
        resources = await client.list_resources()
        result = await client.call_tool("map.search_places", {"query": "东方咖啡"})
    finally:
        await client.close()

    assert len(tools) == 8
    assert [str(resource.uri) for resource in resources] == [
        "map://dataset",
        "map://categories",
    ]

    assert result.structured_content["results"][0]["name"] == "东方咖啡"


def test_update_builds_off_disk_then_rotates_both_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.osm.pbf"
    source.write_bytes(b"old-pbf")
    database = tmp_path / "live" / "china.sqlite"
    database.parent.mkdir()
    database.write_bytes(b"old-database")
    fast_build = tmp_path / "fast-build"

    def fake_run(command: list[str]) -> int:
        if "--outfile" in command:
            output = Path(command[command.index("--outfile") + 1])
            output.write_bytes(source.read_bytes() + b"-updated")
        else:
            build_output = Path(command[2])
            assert build_output.parent == fast_build.resolve()
            build_output.write_bytes(b"new-database")
        return 0

    monkeypatch.setattr(update_map, "_run", fake_run)
    arguments = Namespace(
        source=source,
        database=database,
        work_dir=tmp_path / "work",
        database_build_dir=fast_build,
        updater=Path("updater"),
        server="",
        diff_batch_mb=1,
        socket_timeout=10,
        max_update_batches=2,
        builder_python=Path(sys.executable),
        builder=Path("builder"),
        index_temp_dir=tmp_path / "index",
        build_batch_size=1_000,
    )

    assert update_map.update(arguments) == 0
    assert source.read_bytes() == b"old-pbf-updated"
    assert source.with_name("source.osm.pbf.previous").read_bytes() == b"old-pbf"
    assert database.read_bytes() == b"new-database"
    assert database.with_name("china.sqlite.previous").read_bytes() == b"old-database"
    assert not list(fast_build.glob("*.building"))
