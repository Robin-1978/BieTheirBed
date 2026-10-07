#!/usr/bin/env python3
"""Build the offline map query database from one OSM PBF snapshot.

The builder requires pyosmium. It uses a sparse, disk-backed location index,
commits bounded batches and creates FTS/RTree indexes only after the stream
import has completed.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any

try:
    import osmium
except ImportError as exc:  # pragma: no cover - operator environment
    raise SystemExit(
        "pyosmium is required for index builds; run this script with a Python "
        "environment that provides the 'osmium' module"
    ) from exc

try:
    from .map_service import SCHEMA_VERSION, search_document
    from .taxonomy import category_terms
except ImportError:  # pragma: no cover - direct script execution
    from map_service import SCHEMA_VERSION, search_document
    from taxonomy import category_terms


BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS features(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_type TEXT NOT NULL,
    osm_id INTEGER NOT NULL,
    feature_type TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    subcategory TEXT NOT NULL DEFAULT '',
    admin_level INTEGER NOT NULL DEFAULT 0,
    population INTEGER NOT NULL DEFAULT 0,
    address TEXT NOT NULL DEFAULT '',
    admin_context TEXT NOT NULL DEFAULT '',
    postcode TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    opening_hours TEXT NOT NULL DEFAULT '',
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    min_lat REAL NOT NULL,
    min_lon REAL NOT NULL,
    max_lat REAL NOT NULL,
    max_lon REAL NOT NULL,
    geom BLOB,
    tags_json TEXT NOT NULL DEFAULT '{}',
    search_text TEXT NOT NULL,
    UNIQUE(osm_type, osm_id, feature_type)
);
CREATE TABLE IF NOT EXISTS render_areas(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 osm_type TEXT NOT NULL,
 osm_id INTEGER NOT NULL,
 category TEXT NOT NULL,
 subcategory TEXT NOT NULL DEFAULT '',
 min_lat REAL NOT NULL,
 min_lon REAL NOT NULL,
 max_lat REAL NOT NULL,
 max_lon REAL NOT NULL,
 geom BLOB NOT NULL,
 UNIQUE(osm_type, osm_id)
);
CREATE TABLE IF NOT EXISTS route_nodes(
    id INTEGER PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    modes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS route_edges(
    way_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    source INTEGER NOT NULL,
    target INTEGER NOT NULL,
    length_m REAL NOT NULL,
    forward_modes INTEGER NOT NULL,
    backward_modes INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    highway TEXT NOT NULL DEFAULT '',
    speed_kph REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(way_id, seq)
) WITHOUT ROWID;
"""

INDEX_STATEMENTS = (
    "CREATE INDEX feature_kind ON features(feature_type,category,subcategory)",
    "CREATE VIRTUAL TABLE features_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon)",
    "INSERT INTO features_rtree SELECT id,min_lat,max_lat,min_lon,max_lon FROM features",
    "CREATE VIRTUAL TABLE feature_fts USING fts5(search_text, tokenize='unicode61 remove_diacritics 2')",
    "INSERT INTO feature_fts(rowid,search_text) SELECT id,search_text FROM features",
    "CREATE INDEX render_area_kind ON render_areas(category,subcategory)",
    "CREATE VIRTUAL TABLE render_areas_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon)",
    "INSERT INTO render_areas_rtree SELECT id,min_lat,max_lat,min_lon,max_lon FROM render_areas",
    "CREATE INDEX route_edges_source ON route_edges(source)",
    "CREATE INDEX route_edges_target ON route_edges(target)",
    "CREATE VIRTUAL TABLE route_nodes_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon)",
    "INSERT INTO route_nodes_rtree SELECT id,lat,lat,lon,lon FROM route_nodes",
)

DROP_INDEX_STATEMENTS = (
    "DROP INDEX IF EXISTS feature_identity",
    "DROP INDEX IF EXISTS feature_kind",
    "DROP TABLE IF EXISTS features_rtree",
    "DROP TABLE IF EXISTS feature_fts",
    "DROP INDEX IF EXISTS render_area_kind",
    "DROP TABLE IF EXISTS render_areas_rtree",
    "DROP INDEX IF EXISTS route_edges_source",
    "DROP INDEX IF EXISTS route_edges_target",
    "DROP TABLE IF EXISTS route_nodes_rtree",
)

PRIMARY_CATEGORY_KEYS = (
    "amenity",
    "shop",
    "tourism",
    "leisure",
    "office",
    "healthcare",
    "craft",
    "emergency",
    "public_transport",
    "railway",
    "aeroway",
    "historic",
    "man_made",
    "natural",
)
AREA_CATEGORY_KEYS = ("building", "landuse", "natural", "leisure", "water", "waterway")
DRIVING_HIGHWAYS = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
        "track",
    }
)
WALKING_EXCLUDED = frozenset({"motorway", "motorway_link"})
CYCLING_EXCLUDED = frozenset({"motorway", "motorway_link", "steps"})
DENIED_ACCESS = frozenset({"no", "private"})
TAG_EXPORT_KEYS = frozenset(
    {
        "name",
        "name:zh",
        "name:en",
        "official_name",
        "alt_name",
        "short_name",
        "old_name",
        "brand",
        "place",
        "admin_level",
        "highway",
        "amenity",
        "shop",
        "tourism",
        "leisure",
        "office",
        "healthcare",
        "railway",
        "aeroway",
        "public_transport",
        "building",
        "landuse",
        "natural",
        "addr:province",
        "addr:city",
        "addr:district",
        "addr:subdistrict",
        "addr:street",
        "addr:housenumber",
        "addr:postcode",
        "opening_hours",
        "phone",
        "contact:phone",
        "website",
        "contact:website",
        "population",
    }
)


def _tag(tags: Any, key: str) -> str:
    value = tags.get(key)
    return str(value or "").strip()


def _integer(value: str, default: int = 0) -> int:
    try:
        return int(re.sub(r"[^0-9-]", "", value))
    except ValueError:
        return default


def _primary_category(tags: Any) -> tuple[str, str]:
    for key in PRIMARY_CATEGORY_KEYS:
        value = _tag(tags, key)
        if value:
            return key, value
    return "", ""


def _normalize_area_category(category: str, subcategory: str) -> tuple[str, str]:
    if subcategory.lower() in {"yes", "no", "true", "false", "1", "0"}:
        subcategory = category
    return category, subcategory


def _names(tags: Any) -> tuple[str, str]:
    values = []
    for key in (
        "name",
        "name:zh",
        "name:zh-Hans",
        "official_name",
        "short_name",
        "alt_name",
        "old_name",
        "name:en",
        "brand",
    ):
        for value in _tag(tags, key).split(";"):
            if value and value not in values:
                values.append(value)
    return (values[0] if values else "", "|".join(values[1:]))


def _address(tags: Any) -> tuple[str, str]:
    province = _tag(tags, "addr:province")
    city = _tag(tags, "addr:city")
    district = _tag(tags, "addr:district")
    subdistrict = _tag(tags, "addr:subdistrict")
    street = _tag(tags, "addr:street") or _tag(tags, "addr:place")
    number = _tag(tags, "addr:housenumber")
    address = "".join(value for value in (street, number) if value)
    context = "".join(
        value for value in (province, city, district, subdistrict) if value
    )
    return address, context


def _export_tags(tags: Any) -> str:
    exported = {key: _tag(tags, key) for key in TAG_EXPORT_KEYS if _tag(tags, key)}
    return json.dumps(
        exported, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _point_distance_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    mean_lat = math.radians((a_lat + b_lat) / 2.0)
    dx = math.radians(b_lon - a_lon) * math.cos(mean_lat)
    dy = math.radians(b_lat - a_lat)
    return 6_371_000.0 * math.hypot(dx, dy)


def _parse_speed(value: str) -> float:
    normalized = value.strip().lower()
    match = re.search(r"\d+(?:\.\d+)?", normalized)
    if match is None:
        return 0.0
    speed = float(match.group())
    if "mph" in normalized:
        speed *= 1.609344
    return min(speed, 200.0)


def _route_modes(tags: Any) -> tuple[int, int]:
    highway = _tag(tags, "highway")
    if not highway:
        return 0, 0
    general_access = _tag(tags, "access")
    foot_access = _tag(tags, "foot")
    bicycle_access = _tag(tags, "bicycle")
    motor_access = _tag(tags, "motor_vehicle") or _tag(tags, "motorcar")
    walking = (
        highway not in WALKING_EXCLUDED
        and general_access not in DENIED_ACCESS
        and foot_access not in DENIED_ACCESS
    ) or foot_access in {"yes", "designated", "permissive"}
    cycling = (
        highway not in CYCLING_EXCLUDED
        and general_access not in DENIED_ACCESS
        and bicycle_access not in DENIED_ACCESS
    ) or bicycle_access in {"yes", "designated", "permissive"}
    driving = (
        highway in DRIVING_HIGHWAYS
        and general_access not in DENIED_ACCESS
        and motor_access not in DENIED_ACCESS
    ) or motor_access in {"yes", "designated", "permissive"}
    both = (1 if walking else 0) | (2 if cycling else 0) | (4 if driving else 0)
    forward = both
    backward = both
    oneway = _tag(tags, "oneway").lower()
    if _tag(tags, "junction") == "roundabout" and not oneway:
        oneway = "yes"
    directed_modes = (2 if cycling else 0) | (4 if driving else 0)
    if oneway in {"yes", "1", "true"}:
        backward &= ~directed_modes
    elif oneway == "-1":
        forward &= ~directed_modes
    if _tag(tags, "oneway:bicycle") in {"no", "0", "false"} and cycling:
        forward |= 2
        backward |= 2
    return forward, backward


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_metadata(path: Path) -> dict[str, str]:
    reader = osmium.io.Reader(str(path))
    try:
        header = reader.header()
        box = header.box()
        metadata = {
            "source_name": str(path.resolve()),
            "source_size": str(path.stat().st_size),
            "source_mtime_ns": str(path.stat().st_mtime_ns),
            "replication_timestamp": header.get("osmosis_replication_timestamp") or "",
            "replication_sequence": header.get("osmosis_replication_sequence_number")
            or "",
            "replication_base_url": header.get("osmosis_replication_base_url") or "",
            "bounds": json.dumps(
                {
                    "min_latitude": box.bottom_left.lat,
                    "min_longitude": box.bottom_left.lon,
                    "max_latitude": box.top_right.lat,
                    "max_longitude": box.top_right.lon,
                },
                separators=(",", ":"),
            ),
        }
    finally:
        reader.close()
    metadata["source_sha256"] = _sha256(path)
    return metadata


def _set_metadata(database: sqlite3.Connection, values: Mapping[str, object]) -> None:
    database.executemany(
        "INSERT INTO metadata(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [(key, str(value)) for key, value in values.items()],
    )


class MapImportHandler(osmium.SimpleHandler):
    """Stream OSM objects into bounded SQLite batches."""

    def __init__(self, database: sqlite3.Connection, batch_size: int) -> None:
        super().__init__()
        self.database = database
        self.batch_size = batch_size
        self.wkb = osmium.geom.WKBFactory()
        self.features: list[tuple[Any, ...]] = []
        self.render_areas: list[tuple[Any, ...]] = []
        self.route_nodes: list[tuple[Any, ...]] = []
        self.route_edges: list[tuple[Any, ...]] = []
        self.seen = 0
        self.started = time.monotonic()

    def _feature(
        self,
        *,
        osm_type: str,
        osm_id: int,
        feature_type: str,
        tags: Any,
        latitude: float,
        longitude: float,
        bounds: tuple[float, float, float, float] | None = None,
        geometry: bytes | None = None,
        category: str = "",
        subcategory: str = "",
        name_override: str = "",
    ) -> None:
        name, aliases = _names(tags)
        if name_override:
            name = name_override
        brand = _tag(tags, "brand")
        address, admin_context = _address(tags)
        if feature_type == "address" and not name:
            name = address
        if not name and feature_type != "boundary":
            return
        if bounds is None:
            min_lat = max_lat = latitude
            min_lon = max_lon = longitude
        else:
            min_lat, min_lon, max_lat, max_lon = bounds
        document = search_document(
            name,
            aliases.replace("|", " "),
            brand,
            address,
            admin_context,
            category,
            subcategory,
            " ".join(category_terms(category, subcategory)),
        )
        self.features.append(
            (
                osm_type,
                osm_id,
                feature_type,
                name,
                aliases,
                brand,
                category,
                subcategory,
                _integer(_tag(tags, "admin_level")),
                _integer(_tag(tags, "population")),
                address,
                admin_context,
                _tag(tags, "addr:postcode"),
                _tag(tags, "contact:phone") or _tag(tags, "phone"),
                _tag(tags, "contact:website") or _tag(tags, "website"),
                _tag(tags, "opening_hours"),
                latitude,
                longitude,
                min_lat,
                min_lon,
                max_lat,
                max_lon,
                geometry,
                _export_tags(tags),
                document,
            )
        )

    def _progress(self) -> None:
        self.seen += 1
        if self.seen % 1_000_000 == 0:
            print(
                f"objects={self.seen:,} pending_features={len(self.features):,} "
                f"pending_edges={len(self.route_edges):,} "
                f"elapsed={time.monotonic() - self.started:.0f}s",
                flush=True,
            )

    def flush(self) -> None:
        if not (
            self.features or self.render_areas or self.route_nodes or self.route_edges
        ):
            return
        self.database.execute("BEGIN")
        try:
            if self.features:
                self.database.executemany(
                    "INSERT INTO features("
                    "osm_type,osm_id,feature_type,name,aliases,brand,category,subcategory,"
                    "admin_level,population,address,admin_context,postcode,phone,website,"
                    "opening_hours,lat,lon,min_lat,min_lon,max_lat,max_lon,geom,tags_json,search_text"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(osm_type,osm_id,feature_type) DO UPDATE SET "
                    "name=excluded.name,aliases=excluded.aliases,brand=excluded.brand,"
                    "category=excluded.category,subcategory=excluded.subcategory,"
                    "admin_level=excluded.admin_level,population=excluded.population,"
                    "address=excluded.address,admin_context=excluded.admin_context,"
                    "postcode=excluded.postcode,phone=excluded.phone,website=excluded.website,"
                    "opening_hours=excluded.opening_hours,lat=excluded.lat,lon=excluded.lon,"
                    "min_lat=excluded.min_lat,min_lon=excluded.min_lon,max_lat=excluded.max_lat,"
                    "max_lon=excluded.max_lon,geom=excluded.geom,tags_json=excluded.tags_json,"
                    "search_text=excluded.search_text",
                    self.features,
                )
            if self.render_areas:
                self.database.executemany(
                    "INSERT INTO render_areas(osm_type,osm_id,category,subcategory,"
                    "min_lat,min_lon,max_lat,max_lon,geom) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(osm_type,osm_id) DO UPDATE SET "
                    "category=excluded.category,subcategory=excluded.subcategory,"
                    "min_lat=excluded.min_lat,min_lon=excluded.min_lon,"
                    "max_lat=excluded.max_lat,max_lon=excluded.max_lon,geom=excluded.geom",
                    self.render_areas,
                )
            if self.route_nodes:
                self.database.executemany(
                    "INSERT INTO route_nodes(id,lat,lon,modes) VALUES (?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET lat=excluded.lat,lon=excluded.lon,"
                    "modes=(route_nodes.modes | excluded.modes)",
                    self.route_nodes,
                )
            if self.route_edges:
                self.database.executemany(
                    "INSERT INTO route_edges(way_id,seq,source,target,length_m,forward_modes,"
                    "backward_modes,name,highway,speed_kph) VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(way_id,seq) DO UPDATE SET source=excluded.source,"
                    "target=excluded.target,length_m=excluded.length_m,"
                    "forward_modes=excluded.forward_modes,backward_modes=excluded.backward_modes,"
                    "name=excluded.name,highway=excluded.highway,speed_kph=excluded.speed_kph",
                    self.route_edges,
                )
            self.database.commit()
        except Exception:
            self.database.rollback()
            raise
        self.features.clear()
        self.render_areas.clear()
        self.route_nodes.clear()
        self.route_edges.clear()

    def node(self, node: Any) -> None:
        self._progress()
        if not node.location.valid():
            return
        tags = node.tags
        latitude, longitude = node.location.lat, node.location.lon
        place = _tag(tags, "place")
        if place:
            self._feature(
                osm_type="node",
                osm_id=node.id,
                feature_type="place",
                tags=tags,
                latitude=latitude,
                longitude=longitude,
                category="place",
                subcategory=place,
            )
        category, subcategory = _primary_category(tags)
        if category:
            self._feature(
                osm_type="node",
                osm_id=node.id,
                feature_type="poi",
                tags=tags,
                latitude=latitude,
                longitude=longitude,
                category=category,
                subcategory=subcategory,
            )
        if _tag(tags, "addr:housenumber") or _tag(tags, "addr:street"):
            self._feature(
                osm_type="node",
                osm_id=node.id,
                feature_type="address",
                tags=tags,
                latitude=latitude,
                longitude=longitude,
                category="address",
                subcategory="house",
            )
        if len(self.features) >= self.batch_size:
            self.flush()

    def way(self, way: Any) -> None:
        self._progress()
        tags = way.tags
        locations = [node for node in way.nodes if node.location.valid()]
        if not locations:
            return
        latitude = sum(node.location.lat for node in locations) / len(locations)
        longitude = sum(node.location.lon for node in locations) / len(locations)
        lats = [node.location.lat for node in locations]
        lons = [node.location.lon for node in locations]
        bounds = min(lats), min(lons), max(lats), max(lons)
        geometry = None
        if len(locations) >= 2:
            try:
                geometry = bytes.fromhex(self.wkb.create_linestring(way))
            except (RuntimeError, ValueError):
                geometry = None
        highway = _tag(tags, "highway")
        road_name = _names(tags)[0] or _tag(tags, "ref")
        if highway and road_name:
            self._feature(
                osm_type="way",
                osm_id=way.id,
                feature_type="road",
                tags=tags,
                latitude=latitude,
                longitude=longitude,
                bounds=bounds,
                geometry=geometry,
                category="highway",
                subcategory=highway,
                name_override=road_name,
            )
        category, subcategory = _primary_category(tags)
        if category:
            self._feature(
                osm_type="way",
                osm_id=way.id,
                feature_type="poi",
                tags=tags,
                latitude=latitude,
                longitude=longitude,
                bounds=bounds,
                category=category,
                subcategory=subcategory,
            )
        if _tag(tags, "addr:housenumber"):
            self._feature(
                osm_type="way",
                osm_id=way.id,
                feature_type="address",
                tags=tags,
                latitude=latitude,
                longitude=longitude,
                bounds=bounds,
                category="address",
                subcategory="house",
            )
        forward_modes, backward_modes = _route_modes(tags)
        if (forward_modes or backward_modes) and len(locations) >= 2:
            name = _tag(tags, "name") or _tag(tags, "ref")
            speed = _parse_speed(_tag(tags, "maxspeed"))
            for sequence, (source, target) in enumerate(zip(locations, locations[1:])):
                if source.ref == target.ref:
                    continue
                length = _point_distance_m(
                    source.location.lat,
                    source.location.lon,
                    target.location.lat,
                    target.location.lon,
                )
                if not 0.1 <= length <= 100_000.0:
                    continue
                modes = forward_modes | backward_modes
                self.route_nodes.extend(
                    (
                        (source.ref, source.location.lat, source.location.lon, modes),
                        (target.ref, target.location.lat, target.location.lon, modes),
                    )
                )
                self.route_edges.append(
                    (
                        way.id,
                        sequence,
                        source.ref,
                        target.ref,
                        length,
                        forward_modes,
                        backward_modes,
                        name,
                        highway,
                        speed,
                    )
                )
        if (
            max(len(self.features), len(self.route_edges), len(self.route_nodes) // 2)
            >= self.batch_size
        ):
            self.flush()

    def area(self, area: Any) -> None:
        self._progress()
        tags = area.tags
        boundary = _tag(tags, "boundary") == "administrative"
        area_category = area_subcategory = ""
        for key in AREA_CATEGORY_KEYS:
            if _tag(tags, key):
                area_category, area_subcategory = _normalize_area_category(
                    key, _tag(tags, key)
                )
                break
        if not boundary and not area_category:
            return
        points = [(node.lon, node.lat) for ring in area.outer_rings() for node in ring]
        if not points:
            return
        min_lon = min(point[0] for point in points)
        max_lon = max(point[0] for point in points)
        min_lat = min(point[1] for point in points)
        max_lat = max(point[1] for point in points)
        longitude = sum(point[0] for point in points) / len(points)
        latitude = sum(point[1] for point in points) / len(points)
        try:
            geometry = bytes.fromhex(self.wkb.create_multipolygon(area))
        except (RuntimeError, ValueError):
            return
        osm_type = "way" if area.from_way() else "relation"
        if area_category:
            self.render_areas.append(
                (
                    osm_type,
                    int(area.orig_id()),
                    area_category,
                    area_subcategory,
                    min_lat,
                    min_lon,
                    max_lat,
                    max_lon,
                    geometry,
                )
            )
        self._feature(
            osm_type=osm_type,
            osm_id=int(area.orig_id()),
            feature_type="boundary" if boundary else "area",
            tags=tags,
            latitude=latitude,
            longitude=longitude,
            bounds=(min_lat, min_lon, max_lat, max_lon),
            geometry=geometry,
            category="boundary" if boundary else area_category,
            subcategory=(
                f"admin_level_{_integer(_tag(tags, 'admin_level'))}"
                if boundary
                else area_subcategory
            ),
        )
        if max(len(self.features), len(self.render_areas)) >= self.batch_size:
            self.flush()


def _prepare_database(
    path: Path,
    source: Mapping[str, str],
    resume: bool,
) -> sqlite3.Connection:
    if path.exists() and not resume:
        raise SystemExit(
            f"output database already exists: {path}; use --resume or a new path"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA synchronous=NORMAL")
    database.execute("PRAGMA temp_store=MEMORY")
    database.execute("PRAGMA cache_size=-524288")
    database.executescript(BASE_SCHEMA)
    existing = dict(database.execute("SELECT key,value FROM metadata"))
    if resume and existing:
        for key in ("source_size", "source_mtime_ns", "source_sha256"):
            if existing.get(key) != source.get(key):
                database.close()
                raise SystemExit(
                    "--resume source PBF does not match the partial database"
                )
    _set_metadata(
        database,
        {
            **source,
            "schema_version": SCHEMA_VERSION,
            "build_state": "building",
            "build_started_at": existing.get(
                "build_started_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ),
            "builder": "pyosmium-sparse-v1",
        },
    )
    database.commit()
    return database


def _build_indexes(database: sqlite3.Connection) -> None:
    print("index phase: dropping partial indexes", flush=True)
    for statement in DROP_INDEX_STATEMENTS:
        database.execute(statement)
        database.commit()
    for statement in INDEX_STATEMENTS:
        print(f"index phase: {' '.join(statement.split()[:5])}", flush=True)
        database.execute(statement)
        database.commit()
    database.execute("INSERT INTO feature_fts(feature_fts) VALUES ('optimize')")
    database.execute("ANALYZE")
    database.commit()


def validate_database(database: sqlite3.Connection) -> dict[str, int]:
    """Run deterministic completion checks and return published row counts."""
    quick_check = database.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {quick_check}")
    counts = {
        "feature_count": database.execute("SELECT count(*) FROM features").fetchone()[
            0
        ],
        "render_area_count": database.execute(
            "SELECT count(*) FROM render_areas"
        ).fetchone()[0],
        "route_node_count": database.execute(
            "SELECT count(*) FROM route_nodes"
        ).fetchone()[0],
        "route_edge_count": database.execute(
            "SELECT count(*) FROM route_edges"
        ).fetchone()[0],
        "boundary_count": database.execute(
            "SELECT count(*) FROM features WHERE feature_type='boundary'"
        ).fetchone()[0],
        "place_count": database.execute(
            "SELECT count(*) FROM features WHERE feature_type='place'"
        ).fetchone()[0],
        "poi_count": database.execute(
            "SELECT count(*) FROM features WHERE feature_type='poi'"
        ).fetchone()[0],
        "road_count": database.execute(
            "SELECT count(*) FROM features WHERE feature_type='road'"
        ).fetchone()[0],
        "address_count": database.execute(
            "SELECT count(*) FROM features WHERE feature_type='address'"
        ).fetchone()[0],
    }
    required = (
        "feature_count",
        "route_node_count",
        "route_edge_count",
        "boundary_count",
        "place_count",
        "poi_count",
        "road_count",
    )
    if not all(counts[key] > 0 for key in required):
        raise RuntimeError(f"database is missing required map layers: {counts}")
    rtree_count = database.execute("SELECT count(*) FROM features_rtree").fetchone()[0]
    fts_count = database.execute("SELECT count(*) FROM feature_fts").fetchone()[0]
    render_rtree_count = database.execute(
        "SELECT count(*) FROM render_areas_rtree"
    ).fetchone()[0]
    route_rtree_count = database.execute(
        "SELECT count(*) FROM route_nodes_rtree"
    ).fetchone()[0]
    if rtree_count != counts["feature_count"] or fts_count != counts["feature_count"]:
        raise RuntimeError("feature index row counts do not match the feature table")
    if render_rtree_count != counts["render_area_count"]:
        raise RuntimeError("render area RTree row count does not match render_areas")
    if route_rtree_count != counts["route_node_count"]:
        raise RuntimeError("route node RTree row count does not match route_nodes")
    shanghai = database.execute(
        "SELECT count(*) FROM feature_fts WHERE feature_fts MATCH ?", ('"上海"',)
    ).fetchone()[0]
    if shanghai <= 0:
        raise RuntimeError("representative Chinese FTS query returned no results")
    return counts


def build(args: argparse.Namespace) -> None:
    source_path = args.source.resolve()
    output_path = args.output.resolve()
    if not source_path.is_file():
        raise SystemExit(f"source PBF does not exist: {source_path}")
    source = _source_metadata(source_path)
    print(
        f"source={source_path} size={int(source['source_size']) / (1024**3):.2f}GiB "
        f"replication={source['replication_timestamp']}",
        flush=True,
    )
    database = _prepare_database(output_path, source, args.resume)
    temp_directory = args.temp_dir.resolve()
    temp_directory.mkdir(parents=True, exist_ok=True)
    location_index = temp_directory / f"{output_path.name}.locations.idx"
    if location_index.exists() and not args.resume:
        location_index.unlink()
    handler = MapImportHandler(database, args.batch_size)
    try:
        _set_metadata(database, {"build_phase": "scan"})
        database.commit()
        handler.apply_file(
            str(source_path),
            locations=True,
            idx=f"sparse_file_array,{location_index}",
        )
        handler.flush()
        _set_metadata(database, {"build_phase": "indexes"})
        database.commit()
        _build_indexes(database)
        _set_metadata(database, {"build_phase": "validation"})
        database.commit()
        counts = validate_database(database)
        _set_metadata(
            database,
            {
                **counts,
                "build_state": "ready",
                "build_phase": "complete",
                "build_completed_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            },
        )
        database.commit()
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True), flush=True)
        print(f"database={output_path}", flush=True)
    finally:
        database.close()
    if not args.keep_temp:
        location_index.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source .osm.pbf file")
    parser.add_argument("output", type=Path, help="output SQLite database")
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=Path("/tmp/osm-map-build"),
        help="directory for the sparse node location index",
    )
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    return parser


if __name__ == "__main__":
    parsed_args = _parser().parse_args()
    parsed_args.batch_size = max(1_000, min(parsed_args.batch_size, 500_000))
    try:
        build(parsed_args)
    except KeyboardInterrupt:
        print(
            "build interrupted; rerun with --resume against the same source",
            file=sys.stderr,
        )
        raise SystemExit(130)
