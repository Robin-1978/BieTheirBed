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
from PIL import ImageDraw
import yaml

from examples.osm_map_mcp_server import rebuild_map
from examples.osm_map_mcp_server import incremental_update
from examples.osm_map_mcp_server.map_visual import (
    RoadSegment,
    _background_line_style,
    _draw_roads,
    _road_style,
    buffer_bounds,
    fit_bounds_to_map,
)
from examples.osm_map_mcp_server.map_service import (
    MapService,
    MapSettings,
    _poi_render_priority,
    _road_name,
    _road_render_name,
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
        "source_checkpoint_sequence": "40",
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


def test_background_roads_load_optional_render_levels(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    database = sqlite3.connect(database_path)
    database.execute(
        "ALTER TABLE route_edges ADD COLUMN layer INTEGER NOT NULL DEFAULT 0"
    )
    database.execute(
        "ALTER TABLE route_edges ADD COLUMN structure INTEGER NOT NULL DEFAULT 0"
    )
    database.execute("UPDATE route_edges SET layer=-1,structure=-1 WHERE way_id=100")
    database.commit()
    database.close()

    service = MapService(MapSettings(database_path=database_path))
    roads = service._background_roads((31.199, 121.399, 31.203, 121.403))

    assert roads
    assert {(road.layer, road.structure) for road in roads} == {(-1, -1)}


def test_background_roads_load_complete_intersecting_way_geometry(
    tmp_path: Path,
) -> None:
    service = MapService(MapSettings(database_path=_database(tmp_path)))

    roads = service._background_roads((31.2009, 121.4009, 31.2011, 121.4011))
    named = [road for road in roads if road.name == "测试路"]

    assert len(named) == 2
    assert any(
        road.start == (31.2, 121.4) or road.end == (31.2, 121.4) for road in named
    )


def test_background_roads_large_view_uses_complete_feature_geometry(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    database = sqlite3.connect(database_path)
    database.execute("DROP TABLE route_nodes_rtree")
    database.commit()
    database.close()
    service = MapService(MapSettings(database_path=database_path))

    roads = service._background_roads((31.1, 121.2, 31.3, 121.6))

    named = [road for road in roads if road.name == "测试路"]
    assert len(named) == 2


def test_background_lines_load_complete_intersecting_geometry(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    database = sqlite3.connect(database_path)
    line = _feature(
        6,
        "way",
        300,
        "line",
        "测试河",
        "waterway",
        "canal",
        31.201,
        121.401,
        geometry=_line_wkb([(121.399, 31.199), (121.401, 31.201), (121.403, 31.203)]),
        bounds=(31.199, 121.399, 31.203, 121.403),
    )
    database.execute(
        "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        line,
    )
    database.execute(
        "INSERT INTO features_rtree VALUES (?,?,?,?,?)",
        (line[0], line[19], line[21], line[20], line[22]),
    )
    database.commit()
    database.close()
    service = MapService(MapSettings(database_path=database_path))

    lines = service._background_lines((31.2009, 121.4009, 31.2011, 121.4011))

    assert len(lines) == 1
    assert lines[0].category == "waterway"
    assert lines[0].points == (
        (31.199, 121.399),
        (31.201, 121.401),
        (31.203, 121.403),
    )


def test_background_pois_prioritize_useful_named_features(tmp_path: Path) -> None:
    service = MapService(MapSettings(database_path=_database(tmp_path)))

    pois = service._background_pois((31.199, 121.399, 31.203, 121.403))

    assert [(poi.name, poi.category, poi.subcategory) for poi in pois] == [
        ("上海测试镇", "place", "town"),
        ("东方咖啡", "amenity", "cafe"),
    ]
    assert _poi_render_priority("public_transport", "platform") is None


def test_waterway_lines_use_continuous_area_paint_and_scaled_width() -> None:
    river_fill, river_casing, river_width = _background_line_style(
        "waterway", "river", zoom=14
    )
    canal_fill, canal_casing, canal_width = _background_line_style(
        "waterway", "canal", zoom=14
    )

    assert river_fill == river_casing == canal_fill == canal_casing
    assert river_width == 4
    assert canal_width == 3


def test_map_bounds_expand_to_rendered_aspect_ratio() -> None:
    original = (31.28, 121.158, 31.31, 121.162)
    fitted = fit_bounds_to_map(original)

    assert fitted[0] <= original[0]
    assert fitted[1] < original[1]
    assert fitted[2] >= original[2]
    assert fitted[3] > original[3]


def test_background_query_bounds_extend_beyond_visible_map() -> None:
    visible = fit_bounds_to_map((31.28, 121.158, 31.31, 121.162))
    query = buffer_bounds(visible)

    assert query[0] < visible[0]
    assert query[1] < visible[1]
    assert query[2] > visible[2]
    assert query[3] > visible[3]


def test_joined_road_segments_do_not_leave_casing_seams() -> None:
    class PixelViewport:
        @staticmethod
        def point(x: float, y: float) -> tuple[int, int]:
            return round(x), round(y)

    image = Image.new("RGB", (100, 100), "#ffffff")
    roads = (
        RoadSegment((10, 50), (50, 50), "primary", "测试路"),
        RoadSegment((50, 50), (50, 90), "primary", "测试路"),
    )

    _draw_roads(ImageDraw.Draw(image), PixelViewport(), roads, scale=2)

    # This pixel belongs to the horizontal surface but is also inside the next
    # segment's casing. Drawing each segment casing and surface as a pair leaves
    # a dark block here; class-wide casing and surface passes keep the join clear.
    assert image.getpixel((41, 53)) == (248, 221, 160)


def test_surface_roads_render_above_tunnels() -> None:
    class PixelViewport:
        @staticmethod
        def point(x: float, y: float) -> tuple[int, int]:
            return round(x), round(y)

    image = Image.new("RGB", (100, 100), "#ffffff")
    roads = (
        RoadSegment((10, 50), (90, 50), "motorway", structure=-1, layer=-1),
        RoadSegment((50, 10), (50, 90), "residential"),
    )

    _draw_roads(ImageDraw.Draw(image), PixelViewport(), roads, scale=1)

    assert image.getpixel((50, 50)) == (255, 254, 253)


def test_road_width_tracks_map_zoom() -> None:
    assert _road_style("primary", zoom=14)[2] == 3
    assert _road_style("primary", zoom=16)[2] == 5
    assert _road_style("primary", zoom=18)[2] == 7


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


def test_local_road_name_override_participates_in_search(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    row = _feature(
        6,
        "way",
        424131502,
        "road",
        "",
        "highway",
        "primary",
        31.322,
        121.162,
        geometry=_line_wkb([(121.161, 31.321), (121.163, 31.323)]),
        bounds=(31.321, 121.161, 31.323, 121.163),
    )
    database = sqlite3.connect(database_path)
    database.execute(
        "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        row,
    )
    database.execute(
        "INSERT INTO features_rtree VALUES (?,?,?,?,?)",
        (row[0], row[19], row[21], row[20], row[22]),
    )
    database.execute(
        "INSERT INTO feature_fts(rowid,search_text) VALUES (?,?)", (row[0], row[25])
    )
    database.commit()
    database.close()

    service = MapService(MapSettings(database_path=database_path))
    result = service.search_places("墨玉北路")

    assert result["results"][0]["name"] == "墨玉北路"
    assert result["results"][0]["place_id"] == "osm:way:424131502:road"


def test_station_suffix_uses_station_category_search(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    database = sqlite3.connect(database_path)
    row = _feature(
        6,
        "node",
        6,
        "poi",
        "上海汽车城",
        "railway",
        "station",
        31.2873,
        121.1762,
    )
    database.execute(
        "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        row,
    )
    database.execute(
        "INSERT INTO features_rtree VALUES (?,?,?,?,?)",
        (row[0], row[19], row[21], row[20], row[22]),
    )
    database.execute(
        "INSERT INTO feature_fts(rowid,search_text) VALUES (?,?)",
        (row[0], row[25]),
    )
    database.commit()
    database.close()

    service = MapService(MapSettings(database_path=database_path))
    result = service.search_places("上海汽车城地铁站")

    assert result["results"][0]["name"] == "上海汽车城"
    assert result["results"][0]["category"] == "railway"
    assert result["results"][0]["subcategory"] == "station"


def test_station_resolution_prefers_rail_station_name_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MapService(MapSettings(database_path=tmp_path / "unused.sqlite"))

    def fake_search(query: str, **_kwargs: object) -> dict[str, object]:
        if query == "安亭站":
            places = [
                {
                    "place_id": "osm:node:1:poi",
                    "name": "公交安亭站",
                    "feature_type": "poi",
                    "category": "public_transport",
                    "subcategory": "stop_position",
                    "location": {"latitude": 31.29, "longitude": 121.155},
                }
            ]
        else:
            places = [
                {
                    "place_id": "osm:way:2:line",
                    "name": "安亭",
                    "feature_type": "line",
                    "category": "railway",
                    "subcategory": "station",
                    "location": {"latitude": 31.298, "longitude": 121.162},
                }
            ]
        return {"results": places}

    monkeypatch.setattr(service, "search_places", fake_search)
    candidates = service._resolve_candidates("安亭站", coordinate_system="wgs84")

    assert candidates[0][2]["resolved_query"] == "安亭"
    assert candidates[0][2]["place"]["name"] == "安亭"


def test_road_name_replaces_known_english_only_caoan_segments() -> None:
    assert _road_name(848004000, "Caoan Highway") == "曹安公路"
    assert _road_name(844552657, "Caoan Highway") == "曹安公路"


def test_road_render_name_suppresses_english_only_fallback() -> None:
    assert (
        _road_render_name(
            379526876,
            "Caoan Highway",
            '{"name:en":"Caoan Highway"}',
        )
        == ""
    )
    assert (
        _road_render_name(
            68871845,
            "Caoan Highway",
            '{"name":"曹安公路","name:en":"Caoan Highway"}',
        )
        == "曹安公路"
    )


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
    assert "instead of map.nearby_search" in tools["map.route"].description
    assert (
        "Do not use this tool for 导航/路线" in tools["map.nearby_search"].description
    )
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


def test_dataset_info_separates_current_and_checkpoint_sequences(
    tmp_path: Path,
) -> None:
    service = MapService(MapSettings(database_path=_database(tmp_path)))

    source = service.dataset_info()["source"]

    assert source["replication_sequence"] == "42"
    assert source["pbf_checkpoint_sequence"] == "40"


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

    monkeypatch.setattr(rebuild_map, "_run", fake_run)
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

    assert rebuild_map.update(arguments) == 0
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

    monkeypatch.setattr(rebuild_map, "_run", fake_run)
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

    assert rebuild_map.update(arguments) == 0
    assert source.read_bytes() == b"mirror-pbf"
    assert source.with_name("source.osm.pbf.previous").read_bytes() == b"old-pbf"
    assert database.read_bytes() == b"mirror-database"


def test_update_resumes_completed_pbf_after_database_build_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.osm.pbf"
    source.write_bytes(b"old-pbf")
    database = tmp_path / "live" / "china.sqlite"
    database.parent.mkdir()
    database.write_bytes(b"old-database")
    work = tmp_path / "work"

    def failing_build(command: list[str]) -> int:
        if "--outfile" in command:
            output = Path(command[command.index("--outfile") + 1])
            output.write_bytes(b"new-pbf")
            return 0
        return 1

    monkeypatch.setattr(rebuild_map, "_run", failing_build)
    arguments = Namespace(
        source=source,
        database=database,
        work_dir=work,
        database_build_dir=tmp_path / "fast-build",
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

    with pytest.raises(SystemExit, match="map database build failed"):
        rebuild_map.update(arguments)

    checkpoint = work / "updated-ready.osm.pbf"
    assert checkpoint.read_bytes() == b"new-pbf"
    assert source.read_bytes() == b"old-pbf"
    assert database.read_bytes() == b"old-database"

    def successful_retry(command: list[str]) -> int:
        if "--outfile" in command:
            assert Path(command[1]) == checkpoint.resolve()
            return 0
        assert Path(command[1]) == checkpoint.resolve()
        Path(command[2]).write_bytes(b"new-database")
        return 0

    monkeypatch.setattr(rebuild_map, "_run", successful_retry)
    assert rebuild_map.update(arguments) == 0
    assert source.read_bytes() == b"new-pbf"
    assert database.read_bytes() == b"new-database"
    assert not checkpoint.exists()


def test_incremental_merge_replaces_only_affected_objects(tmp_path: Path) -> None:
    base = tmp_path / "base.sqlite"
    delta = tmp_path / "delta.sqlite"
    source = tmp_path / "updated.osm.pbf"
    source.write_bytes(b"updated-pbf")
    base_schema = """
 CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE features(
 id INTEGER PRIMARY KEY AUTOINCREMENT,osm_type TEXT NOT NULL,osm_id INTEGER NOT NULL,
 feature_type TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',aliases TEXT NOT NULL DEFAULT '',
 brand TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT '',
 subcategory TEXT NOT NULL DEFAULT '',admin_level INTEGER NOT NULL DEFAULT 0,
 population INTEGER NOT NULL DEFAULT 0,address TEXT NOT NULL DEFAULT '',
 admin_context TEXT NOT NULL DEFAULT '',postcode TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '',website TEXT NOT NULL DEFAULT '',
 opening_hours TEXT NOT NULL DEFAULT '',lat REAL NOT NULL,lon REAL NOT NULL,
 min_lat REAL NOT NULL,min_lon REAL NOT NULL,max_lat REAL NOT NULL,max_lon REAL NOT NULL,
 geom BLOB,tags_json TEXT NOT NULL DEFAULT '{}',search_text TEXT NOT NULL,
 UNIQUE(osm_type,osm_id,feature_type));
 CREATE TABLE render_areas(
 id INTEGER PRIMARY KEY AUTOINCREMENT,osm_type TEXT NOT NULL,osm_id INTEGER NOT NULL,
 category TEXT NOT NULL,subcategory TEXT NOT NULL DEFAULT '',min_lat REAL NOT NULL,
 min_lon REAL NOT NULL,max_lat REAL NOT NULL,max_lon REAL NOT NULL,geom BLOB NOT NULL,
 UNIQUE(osm_type,osm_id));
 CREATE TABLE route_nodes(
 id INTEGER PRIMARY KEY,lat REAL NOT NULL,lon REAL NOT NULL,modes INTEGER NOT NULL);
 CREATE TABLE route_edges(
 way_id INTEGER NOT NULL,seq INTEGER NOT NULL,source INTEGER NOT NULL,target INTEGER NOT NULL,
 length_m REAL NOT NULL,forward_modes INTEGER NOT NULL,backward_modes INTEGER NOT NULL,
 name TEXT NOT NULL DEFAULT '',highway TEXT NOT NULL DEFAULT '',
 speed_kph REAL NOT NULL DEFAULT 0,layer INTEGER NOT NULL DEFAULT 0,
 structure INTEGER NOT NULL DEFAULT 0);
 """
    live = sqlite3.connect(base)
    live.executescript(
        base_schema
        + """
 CREATE UNIQUE INDEX feature_identity ON features(osm_type,osm_id,feature_type);
 CREATE INDEX route_edges_source ON route_edges(source);
 CREATE INDEX route_edges_target ON route_edges(target);
 CREATE VIRTUAL TABLE features_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon);
 CREATE VIRTUAL TABLE render_areas_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon);
 CREATE VIRTUAL TABLE feature_fts USING fts5(search_text);
 CREATE VIRTUAL TABLE route_nodes_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon);
 """
    )
    metadata = {
        "schema_version": "1",
        "build_state": "ready",
        "replication_sequence": "10",
        "feature_count": "2",
        "address_count": "0",
        "boundary_count": "0",
        "line_count": "0",
        "place_count": "0",
        "poi_count": "1",
        "road_count": "1",
        "render_area_count": "1",
        "route_edge_count": "2",
        "route_node_count": "3",
    }
    live.executemany("INSERT INTO metadata VALUES(?,?)", metadata.items())
    feature_values = (
        "osm_type,osm_id,feature_type,name,category,subcategory,lat,lon,min_lat,min_lon,"
        "max_lat,max_lon,search_text"
    )
    live.execute(
        f"INSERT INTO features({feature_values}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("way", 10, "road", "旧路", "highway", "residential", 1, 1, 1, 1, 1, 1, "旧路"),
    )
    live.execute(
        f"INSERT INTO features({feature_values}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("node", 100, "poi", "保留点", "amenity", "cafe", 2, 2, 2, 2, 2, 2, "保留点"),
    )
    live.execute(
        "INSERT INTO render_areas(osm_type,osm_id,category,min_lat,min_lon,max_lat,max_lon,geom) "
        "VALUES('way',20,'building',0,0,1,1,x'00')"
    )
    live.executemany(
        "INSERT INTO route_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (10, 0, 1, 2, 1, 1, 1, "旧路", "residential", 20, 0, 0),
            (99, 0, 2, 3, 1, 4, 4, "保留路", "primary", 40, 0, 0),
        ],
    )
    live.executemany(
        "INSERT INTO route_nodes VALUES(?,?,?,?)",
        [(1, 1, 1, 1), (2, 2, 2, 5), (3, 3, 3, 4)],
    )
    live.execute(
        "INSERT INTO features_rtree SELECT id,min_lat,max_lat,min_lon,max_lon FROM features"
    )
    live.execute(
        "INSERT INTO feature_fts(rowid,search_text) SELECT id,search_text FROM features"
    )
    live.execute(
        "INSERT INTO render_areas_rtree SELECT id,min_lat,max_lat,min_lon,max_lon FROM render_areas"
    )
    live.execute(
        "INSERT INTO route_nodes_rtree SELECT id,lat,lat,lon,lon FROM route_nodes"
    )
    live.commit()
    live.close()

    changed = sqlite3.connect(delta)
    changed.executescript(base_schema)
    changed.execute(
        f"INSERT INTO features({feature_values}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("way", 10, "road", "新路", "highway", "secondary", 1, 1, 1, 1, 1, 1, "新路"),
    )
    changed.execute(
        "INSERT INTO route_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (10, 0, 1, 2, 1, 2, 2, "新路", "secondary", 30, 0, 0),
    )
    changed.executemany(
        "INSERT INTO route_nodes VALUES(?,?,?,?)", [(1, 1.1, 1.1, 2), (2, 2.1, 2.1, 2)]
    )
    changed.commit()
    changed.close()

    incremental_update.ensure_incremental_schema(base)
    result = incremental_update.merge_delta(
        base,
        delta,
        incremental_update.Scope(ways={10, 20}),
        source,
        incremental_update.PbfMetadata(11, "2026-10-08T00:00:00Z", "test"),
    )

    live = sqlite3.connect(base)
    assert live.execute(
        "SELECT osm_id,name FROM features ORDER BY osm_id"
    ).fetchall() == [(10, "新路"), (100, "保留点")]
    assert live.execute("SELECT count(*) FROM render_areas").fetchone()[0] == 0
    assert live.execute(
        "SELECT way_id,forward_modes FROM route_edges ORDER BY way_id"
    ).fetchall() == [(10, 2), (99, 4)]
    assert live.execute("SELECT id,modes FROM route_nodes ORDER BY id").fetchall() == [
        (1, 2),
        (2, 6),
        (3, 4),
    ]
    assert (
        live.execute(
            "SELECT value FROM metadata WHERE key='replication_sequence'"
        ).fetchone()[0]
        == "11"
    )
    assert live.execute("SELECT count(*) FROM features_rtree").fetchone()[0] == 2
    assert live.execute("SELECT count(*) FROM feature_fts").fetchone()[0] == 2
    assert result["old_edges"] == result["new_edges"] == 1
    live.close()


def test_dependency_pending_journal_roundtrip(tmp_path: Path) -> None:
    database = tmp_path / "objects.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO metadata VALUES('replication_sequence','10')")
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    scope = incremental_update.Scope(nodes={1, 2}, ways={10}, relations={20})
    incremental_update.stage_dependency_update(
        connection,
        incremental_update.PbfMetadata(11, "2026-10-08T00:00:00Z", "test"),
        10,
        scope,
    )

    pending = incremental_update.pending_dependency_update(database)
    assert pending is not None
    base_sequence, target_sequence, restored_scope = pending
    assert (base_sequence, target_sequence) == (10, 11)
    assert restored_scope == scope
    metadata = incremental_update.dependency_metadata(database)
    assert metadata["replication_sequence"] == "11"
    assert metadata["replication_base_url"] == "test"

    incremental_update.clear_pending_dependency_update(database)
    assert incremental_update.pending_dependency_update(database) is None
    assert (
        incremental_update.dependency_metadata(database)["replication_sequence"] == "11"
    )
