from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from pathlib import Path
import sqlite3
import struct
import sys

import pytest
from PIL import Image
import yaml

from examples.osm_map_mcp_server import update_map
from examples.osm_map_mcp_server.map_service import (
    MapService,
    MapSettings,
    _road_name,
    convert_coordinate,
    search_document,
)
from examples.osm_map_mcp_server.server import _tool_definitions
from examples.osm_map_mcp_server.taxonomy import category_terms
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
    aliases: str = "",
    population: int = 0,
    admin_context: str = "上海市测试区",
) -> tuple:
    min_lat, min_lon, max_lat, max_lon = bounds or (
        latitude,
        longitude,
        latitude,
        longitude,
    )
    document = search_document(
        name,
        aliases,
        brand,
        address,
        admin_context,
        category,
        subcategory,
    )
    return (
        feature_id,
        osm_type,
        osm_id,
        feature_type,
        name,
        aliases,
        brand,
        category,
        subcategory,
        admin_level,
        population,
        address,
        admin_context,
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
    assert "geometry" not in route
    assert route["summary"] == {
        "text": "步行292米，预计4分钟",
        "mode_label": "步行",
        "distance": "292米",
        "duration": "4分钟",
    }
    assert route["steps"] == ["出发，向东北沿测试路行进292米，随后到达目的地"]
    assert route["route_preview"]["coordinates"] == [
        [121.4, 31.2],
        [121.402, 31.202],
    ]
    assert route["navigation"]["label"] == "打开高德地图导航"
    assert "mode=walk" in route["navigation"]["url"]
    assert "coordinate=gaode" in route["navigation"]["url"]

    route_with_geometry = service.route(
        "31.200,121.400",
        "31.202,121.402",
        mode="walking",
        include_geometry=True,
    )
    assert route_with_geometry["geometry"]["type"] == "LineString"
    assert route_with_geometry["geometry"]["coordinates"] == [
        [121.4, 31.2],
        [121.402, 31.202],
    ]

    with pytest.raises(Exception, match="not connected"):
        service.route("31.202,121.402", "31.200,121.400", mode="driving")


def test_spatial_queries_render_valid_managed_pngs(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    service = MapService(
        MapSettings(
            database_path=_database(tmp_path),
            managed_file_root=managed_root,
        )
    )

    results = [
        service.search_places("东方咖啡", include_map_image=True),
        service.nearby_search(
            31.201,
            121.401,
            radius_m=500,
            categories=("amenity:cafe",),
            include_map_image=True,
        ),
        service.reverse_geocode(31.201, 121.401, include_map_image=True),
        service.get_place("osm:way:100:road", include_map_image=True),
        service.route(
            "31.200,121.400",
            "31.202,121.402",
            mode="walking",
            include_map_image=True,
        ),
        service.distance(
            ("31.200,121.400", "31.201,121.401"),
            "31.202,121.402",
            include_map_image=True,
        ),
    ]

    for result in results:
        assert result["visualization"]["type"] == "static_map"
        descriptor = result["managed_file"]
        path = managed_root / descriptor["relative_handle"]
        data = path.read_bytes()
        assert descriptor["kind"] == "managed_file"
        assert descriptor["media_type"] == "image/png"
        assert descriptor["size_bytes"] == len(data)
        assert descriptor["sha256"] == hashlib.sha256(data).hexdigest()
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == (1600, 1000)


def test_background_areas_load_from_legacy_feature_layer(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    database = sqlite3.connect(database_path)
    database.execute(
        "UPDATE features SET feature_type='area',category='natural',"
        "subcategory='water' WHERE id=5"
    )
    database.commit()
    database.close()

    service = MapService(MapSettings(database_path=database_path))
    areas = service._background_areas((31.19, 121.39, 31.23, 121.44))

    assert len(areas) == 1
    assert areas[0].category == "natural"
    assert areas[0].subcategory == "water"
    assert areas[0].rings[0][0] == (31.19, 121.39)


def test_route_map_labels_prioritize_named_major_roads() -> None:
    labels = MapService._route_map_labels(
        [
            {
                "neighbor_lat": 31.001,
                "neighbor_lon": 121.0,
                "name": "墨玉路",
                "highway": "primary",
                "length_m": 100.0,
            },
            {
                "neighbor_lat": 31.002,
                "neighbor_lon": 121.0,
                "name": "墨玉路",
                "highway": "primary",
                "length_m": 100.0,
            },
            {
                "neighbor_lat": 31.002,
                "neighbor_lon": 121.002,
                "name": "宝安公路",
                "highway": "trunk",
                "length_m": 200.0,
            },
        ],
        start_latitude=31.0,
        start_longitude=121.0,
    )

    assert [label.text for label in labels] == ["宝安公路", "墨玉路"]
    moyu = labels[1]
    assert moyu.latitude == pytest.approx(31.001)
    assert moyu.longitude == pytest.approx(121.0)


def test_route_uses_local_name_when_source_way_has_no_name() -> None:
    assert _road_name(424131502, "") == "墨玉北路"
    assert _road_name(424131502, "未来正式名称") == "未来正式名称"

    labels = MapService._route_map_labels(
        [
            {
                "way_id": 424131502,
                "neighbor_lat": 31.322,
                "neighbor_lon": 121.162,
                "name": "墨玉北路",
                "highway": "primary",
                "length_m": 200.0,
            }
        ],
        start_latitude=31.321,
        start_longitude=121.162,
    )

    assert [label.text for label in labels] == ["墨玉北路"]


def test_route_steps_preserve_unnamed_road_types_and_turns() -> None:
    steps = MapService._route_steps(
        [
            {
                "neighbor_lat": 31.0,
                "neighbor_lon": 121.001,
                "name": "",
                "highway": "unclassified",
                "length_m": 100.0,
            },
            {
                "neighbor_lat": 31.001,
                "neighbor_lon": 121.001,
                "name": "",
                "highway": "path",
                "length_m": 110.0,
            },
        ],
        start_latitude=31.0,
        start_longitude=121.0,
    )

    assert steps == [
        "出发，向东沿普通道路行进100米",
        "左转进入步道，向北行进110米，随后到达目的地",
    ]


def test_location_resolution_prefers_real_city_over_local_name(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    extra_rows = [
        _feature(
            6,
            "node",
            6,
            "poi",
            "老君山游客中心",
            "tourism",
            "information",
            33.7484,
            111.6609,
            admin_context="河南省洛阳市栾川县",
        ),
        _feature(
            7,
            "node",
            7,
            "place",
            "上海",
            "place",
            "village",
            33.7500,
            111.6620,
            population=500,
            admin_context="河南省洛阳市栾川县",
        ),
        _feature(
            8,
            "relation",
            8,
            "place",
            "上海市",
            "place",
            "city",
            31.2304,
            121.4737,
            aliases="上海",
            population=24_000_000,
            admin_context="中国",
        ),
    ]
    database = sqlite3.connect(database_path)
    database.executemany(
        "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        extra_rows,
    )
    database.executemany(
        "INSERT INTO features_rtree VALUES (?,?,?,?,?)",
        [(row[0], row[19], row[21], row[20], row[22]) for row in extra_rows],
    )
    database.executemany(
        "INSERT INTO feature_fts(rowid,search_text) VALUES (?,?)",
        [(row[0], row[25]) for row in extra_rows],
    )
    database.commit()
    database.close()

    service = MapService(MapSettings(database_path=database_path))
    search = service.search_places(
        "上海", latitude=33.7484, longitude=111.6609, limit=3
    )
    assert search["results"][0]["name"] == "上海市"
    center_search = service.search_places(
        "上海市中心", latitude=33.7484, longitude=111.6609, limit=3
    )
    assert center_search["results"][0]["name"] == "上海市"

    candidates = service._resolve_candidates(
        "上海市中心",
        coordinate_system="wgs84",
        bias_latitude=33.7484,
        bias_longitude=111.6609,
    )
    assert candidates[0][2]["resolved_query"] == "上海市"
    assert candidates[0][2]["place"]["name"] == "上海市"


def test_map_tool_guidance_and_fruit_taxonomy() -> None:
    tools = {tool.name: tool for tool in _tool_definitions()}
    assert "怎么走" in tools["map.route"].description
    assert "附近" in tools["map.nearby_search"].description
    assert tools["map.route"].input_schema["properties"]["include_geometry"] == {
        "type": "boolean",
        "default": False,
        "description": (
            "Return route line coordinates for map rendering. Leave false for "
            "ordinary directions so the result stays compact."
        ),
    }
    for name in (
        "map.search_places",
        "map.reverse_geocode",
        "map.nearby_search",
        "map.get_place",
        "map.route",
        "map.distance",
    ):
        assert (
            tools[name].input_schema["properties"]["include_map_image"]["default"]
            is True
        )
    assert "水果" in category_terms("shop", "greengrocer")


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


def test_update_can_bootstrap_a_new_replication_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.osm.pbf"
    source.write_bytes(b"old-pbf")
    bootstrap = tmp_path / "mirror.osm.pbf"
    bootstrap.write_bytes(b"mirror-pbf")
    database = tmp_path / "live" / "china.sqlite"
    database.parent.mkdir()
    database.write_bytes(b"old-database")
    fast_build = tmp_path / "fast-build"

    def fake_run(command: list[str]) -> int:
        if "--outfile" not in command:
            assert Path(command[1]) == bootstrap.resolve()
            Path(command[2]).write_bytes(b"mirror-database")
        return 0

    monkeypatch.setattr(update_map, "_run", fake_run)
    arguments = Namespace(
        source=source,
        database=database,
        bootstrap_pbf=bootstrap,
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
    assert source.read_bytes() == b"mirror-pbf"
    assert source.with_name("source.osm.pbf.previous").read_bytes() == b"old-pbf"
    assert database.read_bytes() == b"mirror-database"
