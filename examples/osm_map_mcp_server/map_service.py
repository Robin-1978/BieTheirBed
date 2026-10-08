"""Read-only query and routing service for the offline OSM map database."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import time
from typing import Any, Iterable, Sequence
import unicodedata
from urllib.parse import urlencode

try:
    from .map_visual import (
        BackgroundLine,
        BackgroundPoi,
        MapCircle,
        MapLabel,
        MapLine,
        MapMarker,
        MapPolygon,
        RoadSegment,
        buffer_bounds,
        fit_bounds_to_map,
        render_static_map,
        scene_bounds,
    )
    from .taxonomy import category_inventory
except ImportError:  # pragma: no cover - direct script execution
    from map_visual import (
        BackgroundLine,
        BackgroundPoi,
        MapCircle,
        MapLabel,
        MapLine,
        MapMarker,
        MapPolygon,
        RoadSegment,
        buffer_bounds,
        fit_bounds_to_map,
        render_static_map,
        scene_bounds,
    )
    from taxonomy import category_inventory


SCHEMA_VERSION = "1"
MODE_BITS = {"walking": 1, "cycling": 2, "driving": 4}
SUPPORTED_COORDINATE_SYSTEMS = frozenset({"wgs84", "gcj02", "bd09"})
_EARTH_RADIUS_M = 6_371_000.0
_COORDINATE_RE = re.compile(
    r"(?<![\d.])(-?\d{1,2}(?:\.\d+)?)\s*[,，]\s*"
    r"(-?\d{1,3}(?:\.\d+)?)(?![\d.])"
)
_SEARCH_PART_RE = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+", re.IGNORECASE)
logger = logging.getLogger("osm-map-service")

# Local corrections for OSM ways whose current source tags omit the local name
# or expose only an English fallback.
_ROAD_NAME_OVERRIDES = {
    27583036: "墨玉路",
    379526884: "墨玉路",
    1338590373: "墨玉路",
    1338590376: "墨玉路",
    340586353: "墨玉路",
    340586333: "墨玉北路",
    340586335: "墨玉北路",
    340586339: "墨玉北路",
    340586341: "墨玉北路",
    340586351: "墨玉北路",
    340586352: "墨玉北路",
    424131498: "墨玉北路",
    424131502: "墨玉北路",
    424131505: "墨玉北路",
    605963698: "墨玉北路",
    605963700: "墨玉北路",
    605975230: "墨玉北路",
    605975231: "墨玉北路",
    1299162292: "墨玉北路",
    1299162328: "墨玉北路",
    848004000: "曹安公路",
    844552657: "曹安公路",
}


_POI_RENDER_EXCLUDED = {
    ("public_transport", "platform"),
    ("public_transport", "stop_position"),
    ("railway", "rail"),
    ("man_made", "bridge"),
}
_POI_RENDER_PRIORITY = {
    ("railway", "subway"): 0,
    ("railway", "station"): 0,
    ("public_transport", "station"): 0,
    ("amenity", "hospital"): 0,
    ("place", "town"): 1,
    ("place", "village"): 1,
    ("amenity", "university"): 1,
    ("amenity", "college"): 1,
    ("amenity", "school"): 2,
    ("amenity", "kindergarten"): 3,
    ("shop", "mall"): 2,
    ("shop", "supermarket"): 3,
    ("leisure", "park"): 2,
    ("tourism", "attraction"): 2,
    ("tourism", "museum"): 2,
    ("tourism", "hotel"): 4,
    ("amenity", "fuel"): 4,
    ("amenity", "parking"): 5,
    ("amenity", "bank"): 5,
    ("amenity", "pharmacy"): 5,
    ("amenity", "restaurant"): 7,
    ("amenity", "cafe"): 7,
    ("amenity", "fast_food"): 8,
}
_POI_CATEGORY_PRIORITY = {
    "place": 3,
    "railway": 4,
    "public_transport": 4,
    "amenity": 9,
    "shop": 10,
    "tourism": 8,
    "leisure": 8,
    "office": 11,
}


def _poi_render_priority(category: str, subcategory: str) -> int | None:
    identity = (category, subcategory)
    if identity in _POI_RENDER_EXCLUDED:
        return None
    return _POI_RENDER_PRIORITY.get(
        identity,
        _POI_CATEGORY_PRIORITY.get(category),
    )


def _road_name(way_id: int, source_name: object) -> str:
    name = str(source_name or "").strip()
    override = _ROAD_NAME_OVERRIDES.get(way_id, "")
    has_local_script = any("\u3400" <= character <= "\u9fff" for character in name)
    return override if override and not has_local_script else name


def _feature_name(row: sqlite3.Row) -> str:
    name = str(row["name"] or "").strip()
    if row["feature_type"] == "road" and row["osm_type"] == "way":
        return _road_name(int(row["osm_id"]), name)
    return name


def _road_render_name(
    way_id: int,
    source_name: object,
    tags_json: object,
) -> str:
    """Prefer a road's local OSM name and suppress English-only fallbacks."""

    try:
        tags = json.loads(str(tags_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        tags = {}
    if not isinstance(tags, dict):
        tags = {}
    for key in ("name", "name:zh"):
        if local_name := str(tags.get(key, "")).strip():
            return _road_name(way_id, local_name)
    source = str(source_name or "").strip()
    english_name = str(tags.get("name:en", "")).strip()
    if english_name and source == english_name:
        return _ROAD_NAME_OVERRIDES.get(way_id, "")
    return _road_name(way_id, source)


_ROAD_LEVEL_OVERRIDES = {68871851: (-1, -1)}


def _road_render_level(way_id: int, tags_json: object) -> tuple[int, int]:
    override = _ROAD_LEVEL_OVERRIDES.get(way_id)
    if override is not None:
        return override
    try:
        tags = json.loads(str(tags_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        tags = {}
    if not isinstance(tags, dict):
        tags = {}
    match = re.search(r"-?\d+", str(tags.get("layer", "")))
    layer = int(match.group()) if match else 0

    def enabled(key: str) -> bool:
        return str(tags.get(key, "")).strip().lower() not in {
            "",
            "0",
            "false",
            "no",
        }

    structure = -1 if enabled("tunnel") else 1 if enabled("bridge") else 0
    return layer, structure


class MapError(RuntimeError):
    """Stable, user-visible map failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


@dataclass(frozen=True)
class MapSettings:
    database_path: Path
    route_timeout_seconds: float = 20.0
    route_max_expansions: int = 250_000
    managed_file_root: Path | None = None

    @classmethod
    def from_env(cls) -> "MapSettings":
        configured = os.environ.get("OSM_MAP_DB", "").strip()
        path = (
            Path(configured).expanduser()
            if configured
            else Path("~/.local/share/knoa/osm-map/china.sqlite").expanduser()
        )
        return cls(
            database_path=path,
            route_timeout_seconds=_bounded_float(
                os.environ.get("OSM_MAP_ROUTE_TIMEOUT_SECONDS", "20"), 1.0, 60.0
            ),
            route_max_expansions=_bounded_int(
                os.environ.get("OSM_MAP_ROUTE_MAX_EXPANSIONS", "250000"),
                1_000,
                1_000_000,
            ),
            managed_file_root=(
                Path(managed_root).expanduser()
                if (
                    managed_root := os.environ.get(
                        "KNOA_MCP_MANAGED_FILE_ROOT", ""
                    ).strip()
                )
                else None
            ),
        )


def _bounded_float(value: object, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid numeric setting") from exc
    return max(lower, min(parsed, upper))


def _bounded_int(value: object, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid integer setting") from exc
    return max(lower, min(parsed, upper))


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def search_tokens(value: str) -> list[str]:
    """Create deterministic Chinese bigrams and Latin tokens for FTS."""
    tokens: list[str] = []
    for part in _SEARCH_PART_RE.findall(_normalize_text(value)):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
        else:
            tokens.append(part)
    return list(dict.fromkeys(tokens))


def search_document(*values: str) -> str:
    """Build the text stored in the unicode61 FTS index."""
    raw = " ".join(_normalize_text(value) for value in values if value)
    grams = " ".join(search_tokens(raw))
    return f"{raw} {grams}".strip()


def _fts_expression(value: str) -> str:
    tokens = search_tokens(value)
    if not tokens:
        raise MapError("invalid_query", "query must contain searchable text")
    pieces = []
    for token in tokens[:32]:
        escaped = token.replace('"', '""')
        suffix = "*" if len(token) == 1 or token.isascii() else ""
        pieces.append(f'"{escaped}"{suffix}')
    return " AND ".join(pieces)


def _normalize_category(category: str, subcategory: str) -> tuple[str, str]:
    """Normalize raw OSM category values for stable query results.

    The production database already contains rows like
    ``category=building subcategory=yes``. Rebuilding 21 GB is expensive,
    so normalize at query time and fix the builders for future builds.
    """
    category = (category or "").strip()
    subcategory = (subcategory or "").strip()
    if subcategory.lower() in {"yes", "no", "true", "false", "1", "0"}:
        subcategory = category or subcategory
    return category, subcategory


def _strip_admin_suffix(value: str) -> str:
    """Strip trailing Chinese admin suffixes so 上海 matches 上海市."""
    stripped = _normalize_text(value)
    for _ in range(3):
        for suffix in (
            "市",
            "省",
            "区",
            "县",
            "旗",
            "州",
            "盟",
            "镇",
            "乡",
            "街道",
            "村",
        ):
            if stripped.endswith(suffix) and len(stripped) > 2:
                stripped = stripped[: -len(suffix)]
                break
        else:
            break
    return stripped


def _region_name_score(row_name: str, query: str) -> int:
    """Score region name match: 0 exact (suffix-insensitive), 1 contains, 2 other."""
    row_norm = _normalize_text(row_name)
    query_norm = _normalize_text(query)
    if not row_norm or not query_norm:
        return 2
    if row_norm == query_norm or _strip_admin_suffix(row_norm) == _strip_admin_suffix(
        query_norm
    ):
        return 0
    if query_norm in row_norm or row_norm in query_norm:
        return 1
    stripped_row = _strip_admin_suffix(row_norm)
    stripped_query = _strip_admin_suffix(query_norm)
    if stripped_query and (
        stripped_query in stripped_row or stripped_row in stripped_query
    ):
        return 1

    return 2


def _location_search_query(value: str) -> str:
    """Remove generic urban-center wording without changing real POI names."""
    cleaned = value.strip()
    for suffix in ("中心城区", "市中心", "市区"):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
            if suffix == "市中心":
                return cleaned[: -len("中心")]
            if suffix == "市区":
                return cleaned[:-1]
            return cleaned[: -len(suffix)]
    return cleaned


def _location_search_queries(value: str) -> list[str]:
    """Return the literal location query plus useful station-name variants."""
    primary = _location_search_query(value)
    queries = [primary]
    station_query = _station_search_query(primary)
    if station_query:
        queries.append(station_query)
    return list(dict.fromkeys(query for query in queries if query))


def _station_search_query(value: str) -> str:
    """Return the name part of an explicit public-transport station query."""
    primary = _location_search_query(value)
    for suffix in ("地铁站", "火车站", "车站", "站"):
        if primary.endswith(suffix) and len(primary) > len(suffix):
            return primary[: -len(suffix)]
    return ""


def _bounds_area(bounds: tuple[float, float, float, float]) -> float:
    min_lat, min_lon, max_lat, max_lon = bounds
    return max(0.0, max_lat - min_lat) * max(0.0, max_lon - min_lon)


def _validate_coordinate(latitude: float, longitude: float) -> tuple[float, float]:
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise MapError("invalid_coordinate", "coordinates must be finite")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise MapError("invalid_coordinate", "coordinate is outside the valid range")
    return latitude, longitude


def _haversine_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _outside_china(latitude: float, longitude: float) -> bool:
    return not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271)


def _transform_latitude(x: float, y: float) -> float:
    result = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    result += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    result += (20 * math.sin(y * math.pi) + 40 * math.sin(y / 3 * math.pi)) * 2 / 3
    result += (
        (160 * math.sin(y / 12 * math.pi) + 320 * math.sin(y * math.pi / 30)) * 2 / 3
    )
    return result


def _transform_longitude(x: float, y: float) -> float:
    result = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    result += (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    result += (20 * math.sin(x * math.pi) + 40 * math.sin(x / 3 * math.pi)) * 2 / 3
    result += (
        (150 * math.sin(x / 12 * math.pi) + 300 * math.sin(x / 30 * math.pi)) * 2 / 3
    )
    return result


def _wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    if _outside_china(latitude, longitude):
        return latitude, longitude
    d_lat = _transform_latitude(longitude - 105, latitude - 35)
    d_lon = _transform_longitude(longitude - 105, latitude - 35)
    rad_lat = math.radians(latitude)
    magic = 1 - 0.00669342162296594323 * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (
        d_lat
        * 180
        / ((6_378_245 * (1 - 0.00669342162296594323)) / (magic * sqrt_magic) * math.pi)
    )
    d_lon = d_lon * 180 / (6_378_245 / sqrt_magic * math.cos(rad_lat) * math.pi)
    return latitude + d_lat, longitude + d_lon


def _gcj02_to_wgs84(latitude: float, longitude: float) -> tuple[float, float]:
    if _outside_china(latitude, longitude):
        return latitude, longitude
    guess_lat, guess_lon = latitude, longitude
    for _ in range(6):
        converted_lat, converted_lon = _wgs84_to_gcj02(guess_lat, guess_lon)
        guess_lat -= converted_lat - latitude
        guess_lon -= converted_lon - longitude
    return guess_lat, guess_lon


def _gcj02_to_bd09(latitude: float, longitude: float) -> tuple[float, float]:
    radius = math.hypot(longitude, latitude) + 0.00002 * math.sin(
        latitude * math.pi * 3000 / 180
    )
    theta = math.atan2(latitude, longitude) + 0.000003 * math.cos(
        longitude * math.pi * 3000 / 180
    )
    return radius * math.sin(theta) + 0.006, radius * math.cos(theta) + 0.0065


def _bd09_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    x, y = longitude - 0.0065, latitude - 0.006
    radius = math.hypot(x, y) - 0.00002 * math.sin(y * math.pi * 3000 / 180)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * math.pi * 3000 / 180)
    return radius * math.sin(theta), radius * math.cos(theta)


def convert_coordinate(
    latitude: float,
    longitude: float,
    source: str,
    target: str,
) -> tuple[float, float]:
    latitude, longitude = _validate_coordinate(float(latitude), float(longitude))
    source, target = source.lower().replace("-", ""), target.lower().replace("-", "")
    if (
        source not in SUPPORTED_COORDINATE_SYSTEMS
        or target not in SUPPORTED_COORDINATE_SYSTEMS
    ):
        raise MapError(
            "invalid_coordinate_system", "supported systems are wgs84, gcj02 and bd09"
        )
    if source == target:
        return latitude, longitude
    if source == "gcj02":
        wgs_lat, wgs_lon = _gcj02_to_wgs84(latitude, longitude)
    elif source == "bd09":
        gcj_lat, gcj_lon = _bd09_to_gcj02(latitude, longitude)
        wgs_lat, wgs_lon = _gcj02_to_wgs84(gcj_lat, gcj_lon)
    else:
        wgs_lat, wgs_lon = latitude, longitude
    if target == "wgs84":
        return wgs_lat, wgs_lon
    gcj_lat, gcj_lon = _wgs84_to_gcj02(wgs_lat, wgs_lon)
    return (gcj_lat, gcj_lon) if target == "gcj02" else _gcj02_to_bd09(gcj_lat, gcj_lon)


class _WKBReader:
    def __init__(self, data: bytes) -> None:
        self.data = memoryview(data)
        self.offset = 0

    def _unpack(self, fmt: str, endian: str) -> tuple[Any, ...]:
        size = struct.calcsize(endian + fmt)
        if self.offset + size > len(self.data):
            raise ValueError("truncated WKB")
        values = struct.unpack_from(endian + fmt, self.data, self.offset)
        self.offset += size
        return values

    def geometry(self) -> tuple[str, Any]:
        if self.offset >= len(self.data):
            raise ValueError("empty WKB")
        byte_order = int(self.data[self.offset])
        self.offset += 1
        endian = "<" if byte_order == 1 else ">" if byte_order == 0 else ""
        if not endian:
            raise ValueError("invalid WKB byte order")
        (raw_type,) = self._unpack("I", endian)
        if raw_type & 0x20000000:
            self._unpack("I", endian)
        geometry_type = (raw_type & 0x0FFFFFFF) % 1000
        if geometry_type == 1:
            return "Point", self._unpack("dd", endian)
        if geometry_type == 2:
            (count,) = self._unpack("I", endian)
            return "LineString", [self._unpack("dd", endian) for _ in range(count)]
        if geometry_type == 3:
            (ring_count,) = self._unpack("I", endian)
            rings = []
            for _ in range(ring_count):
                (count,) = self._unpack("I", endian)
                rings.append([self._unpack("dd", endian) for _ in range(count)])
            return "Polygon", rings
        if geometry_type in {4, 5, 6, 7}:
            (count,) = self._unpack("I", endian)
            children = [self.geometry() for _ in range(count)]
            name = {
                4: "MultiPoint",
                5: "MultiLineString",
                6: "MultiPolygon",
                7: "GeometryCollection",
            }
            return name[geometry_type], children
        raise ValueError(f"unsupported WKB geometry type {geometry_type}")


def _read_wkb(data: bytes | None) -> tuple[str, Any] | None:
    return None if not data else _WKBReader(data).geometry()


def _point_in_ring(longitude: float, latitude: float, ring: Sequence[Any]) -> bool:
    inside = False
    previous = len(ring) - 1
    for current in range(len(ring)):
        x_cur, y_cur = ring[current]
        x_prev, y_prev = ring[previous]
        if (y_cur > latitude) != (y_prev > latitude):
            crossing = (x_prev - x_cur) * (latitude - y_cur) / (y_prev - y_cur) + x_cur
            if longitude < crossing:
                inside = not inside
        previous = current
    return inside


def _geometry_contains(
    geometry: tuple[str, Any] | None, lon: float, lat: float
) -> bool:
    if geometry is None:
        return False
    kind, value = geometry
    if kind == "Polygon":
        return bool(
            value
            and _point_in_ring(lon, lat, value[0])
            and not any(_point_in_ring(lon, lat, ring) for ring in value[1:])
        )
    if kind in {"MultiPolygon", "GeometryCollection"}:
        return any(_geometry_contains(child, lon, lat) for child in value)
    return False


def _iter_lines(geometry: tuple[str, Any] | None) -> Iterable[Sequence[Any]]:
    if geometry is None:
        return
    kind, value = geometry
    if kind == "LineString":
        yield value
    elif kind == "Polygon":
        yield from value
    elif kind in {"MultiLineString", "MultiPolygon", "GeometryCollection"}:
        for child in value:
            yield from _iter_lines(child)


def _iter_polygons(
    geometry: tuple[str, Any] | None,
) -> Iterable[Sequence[Sequence[Any]]]:
    if geometry is None:
        return
    kind, value = geometry
    if kind == "Polygon":
        yield value
    elif kind in {"MultiPolygon", "GeometryCollection"}:
        for child in value:
            yield from _iter_polygons(child)


def _point_segment_distance_m(
    lat: float, lon: float, a: Sequence[float], b: Sequence[float]
) -> float:
    scale_x, scale_y = math.cos(math.radians(lat)) * 111_320, 110_540
    ax, ay = (a[0] - lon) * scale_x, (a[1] - lat) * scale_y
    bx, by = (b[0] - lon) * scale_x, (b[1] - lat) * scale_y
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(ax, ay)
    ratio = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + ratio * dx, ay + ratio * dy)


def _distance_to_geometry_m(
    lat: float, lon: float, geometry: tuple[str, Any] | None
) -> float | None:
    if geometry is None:
        return None
    if geometry[0] == "Point":
        point = geometry[1]
        return _haversine_m(lat, lon, point[1], point[0])
    best: float | None = None
    for line in _iter_lines(geometry):
        for index in range(1, len(line)):
            distance = _point_segment_distance_m(lat, lon, line[index - 1], line[index])
            best = distance if best is None else min(best, distance)
    return best


def _place_id(row: sqlite3.Row) -> str:
    return f"osm:{row['osm_type']}:{row['osm_id']}:{row['feature_type']}"


def _feature_result(
    row: sqlite3.Row,
    *,
    distance_m: float | None = None,
    coordinate_system: str = "wgs84",
    details: bool = False,
) -> dict[str, Any]:
    latitude, longitude = convert_coordinate(
        float(row["lat"]), float(row["lon"]), "wgs84", coordinate_system
    )
    category, subcategory = _normalize_category(row["category"], row["subcategory"])
    result: dict[str, Any] = {
        "place_id": _place_id(row),
        "name": _feature_name(row),
        "feature_type": row["feature_type"],
        "category": category,
        "subcategory": subcategory,
        "location": {
            "latitude": round(latitude, 7),
            "longitude": round(longitude, 7),
            "coordinate_system": coordinate_system,
        },
    }
    for key in ("brand", "address", "admin_context"):
        if row[key]:
            result[key] = row[key]
    if row["admin_level"]:
        result["admin_level"] = int(row["admin_level"])
    if distance_m is not None:
        result["distance_m"] = round(distance_m)
    if details:
        result["aliases"] = [part for part in row["aliases"].split("|") if part]
        result["bbox_wgs84"] = {
            "min_latitude": row["min_lat"],
            "min_longitude": row["min_lon"],
            "max_latitude": row["max_lat"],
            "max_longitude": row["max_lon"],
        }
        result["contact"] = {
            key: row[key]
            for key in ("postcode", "phone", "website", "opening_hours")
            if row[key]
        }
        result["osm"] = {"type": row["osm_type"], "id": int(row["osm_id"])}
    return result


def _douglas_peucker(
    points: list[tuple[float, float]], tolerance_m: float
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    maximum, split_index = -1.0, 0
    for index in range(1, len(points) - 1):
        distance = _point_segment_distance_m(
            points[index][0],
            points[index][1],
            (start[1], start[0]),
            (end[1], end[0]),
        )
        if distance > maximum:
            maximum, split_index = distance, index
    if maximum <= tolerance_m:
        return [start, end]
    left = _douglas_peucker(points[: split_index + 1], tolerance_m)
    right = _douglas_peucker(points[split_index:], tolerance_m)
    return left[:-1] + right


_HIGHWAY_LABELS = {
    "motorway": "高速公路",
    "trunk": "干道",
    "primary": "主干道",
    "secondary": "次干道",
    "tertiary": "地方道路",
    "unclassified": "普通道路",
    "residential": "居民道路",
    "living_street": "生活道路",
    "service": "辅路",
    "track": "乡间道路",
    "path": "步道",
    "footway": "人行道",
    "pedestrian": "步行街",
    "cycleway": "自行车道",
    "steps": "台阶",
    "road": "道路",
}
_MODE_LABELS = {"walking": "步行", "cycling": "骑行", "driving": "驾车"}


def _bearing_degrees(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> float:
    start_phi, end_phi = math.radians(start_latitude), math.radians(end_latitude)
    delta_lon = math.radians(end_longitude - start_longitude)
    y = math.sin(delta_lon) * math.cos(end_phi)
    x = math.cos(start_phi) * math.sin(end_phi) - math.sin(start_phi) * math.cos(
        end_phi
    ) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _compass_direction(bearing: float) -> str:
    directions = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
    return directions[int((bearing + 22.5) // 45) % len(directions)]


def _turn_directive(previous_bearing: float, next_bearing: float) -> tuple[str, str]:
    delta = (next_bearing - previous_bearing + 540.0) % 360.0 - 180.0
    magnitude = abs(delta)
    if magnitude < 20:
        return "continue", "继续直行"
    if magnitude < 45:
        return (
            ("slight_right", "稍向右转") if delta > 0 else ("slight_left", "稍向左转")
        )
    if magnitude < 135:
        return ("turn_right", "右转") if delta > 0 else ("turn_left", "左转")
    return "uturn", "掉头"


def _format_distance_zh(distance_m: float) -> str:
    if distance_m < 1_000:
        return f"{max(0, round(distance_m))}米"
    return f"{distance_m / 1_000:.1f}".rstrip("0").rstrip(".") + "公里"


def _format_duration_zh(duration_seconds: float) -> str:
    minutes = max(1, round(duration_seconds / 60))
    if minutes < 60:
        return f"{minutes}分钟"
    hours, remaining = divmod(minutes, 60)
    return f"{hours}小时{remaining}分钟" if remaining else f"{hours}小时"


def _route_match_label(match: dict[str, Any], fallback: str) -> str:
    place = match.get("place")
    if isinstance(place, dict) and str(place.get("name") or "").strip():
        return str(place["name"]).strip()
    return fallback


def _route_preview(
    points: list[tuple[float, float]],
    distance_m: float,
    output_coordinate_system: str,
    *,
    max_points: int = 8,
) -> dict[str, Any]:
    if not points:
        return {
            "type": "LineString",
            "coordinates": [],
            "coordinate_system": output_coordinate_system,
        }
    simplified = _douglas_peucker(points, max(4.0, distance_m / 80_000))
    if len(simplified) > max_points:
        stride = math.ceil((len(simplified) - 1) / (max_points - 1))
        simplified = simplified[::stride]
    if simplified[-1] != points[-1]:
        simplified.append(points[-1])
    coordinates = []
    for latitude, longitude in simplified:
        converted_lat, converted_lon = convert_coordinate(
            latitude, longitude, "wgs84", output_coordinate_system
        )
        coordinates.append([round(converted_lon, 7), round(converted_lat, 7)])
    return {
        "type": "LineString",
        "coordinates": coordinates,
        "coordinate_system": output_coordinate_system,
    }


def _amap_navigation_url(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    *,
    origin_name: str,
    destination_name: str,
    mode: str,
) -> str:
    origin_gcj = convert_coordinate(origin_latitude, origin_longitude, "wgs84", "gcj02")
    destination_gcj = convert_coordinate(
        destination_latitude, destination_longitude, "wgs84", "gcj02"
    )
    query = urlencode(
        {
            "from": f"{origin_gcj[1]:.7f},{origin_gcj[0]:.7f},{origin_name}",
            "to": (
                f"{destination_gcj[1]:.7f},{destination_gcj[0]:.7f},{destination_name}"
            ),
            "mode": {"walking": "walk", "cycling": "ride", "driving": "car"}[mode],
            "policy": "1",
            "src": "knoa",
            "coordinate": "gaode",
            "callnative": "1",
        },
        safe=",",
    )
    return f"https://uri.amap.com/navigation?{query}"


class MapService:
    """Bounded read-only access to one completed map database."""

    def __init__(self, settings: MapSettings) -> None:
        self.settings = settings

    def _connect(self, *, require_ready: bool = True) -> sqlite3.Connection:
        path = self.settings.database_path
        if not path.is_file():
            raise MapError("dataset_missing", f"map database does not exist: {path}")
        database = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA query_only=ON")
        try:
            metadata = dict(database.execute("SELECT key,value FROM metadata"))
        except sqlite3.DatabaseError as exc:
            database.close()
            raise MapError(
                "dataset_invalid", "map database metadata is unreadable"
            ) from exc
        if metadata.get("schema_version") != SCHEMA_VERSION:
            database.close()
            raise MapError(
                "dataset_incompatible", "map database schema version is incompatible"
            )
        if require_ready and metadata.get("build_state") != "ready":
            database.close()
            raise MapError("dataset_not_ready", "map database build has not completed")
        return database

    @staticmethod
    def _metadata(database: sqlite3.Connection) -> dict[str, str]:
        return dict(database.execute("SELECT key,value FROM metadata"))

    def _background_roads(
        self,
        bounds: tuple[float, float, float, float],
        *,
        limit: int = 8_000,
    ) -> list[RoadSegment]:
        min_lat, min_lon, max_lat, max_lon = bounds
        if _haversine_m(min_lat, min_lon, max_lat, max_lon) > 80_000:
            return []
        grid_size = 8
        cells = grid_size * grid_size
        major_per_cell = max(200, limit // cells)
        detail_per_cell = max(60, limit // cells // 2)
        database = self._connect()
        edge_columns = {
            str(row[1]) for row in database.execute("PRAGMA table_info(route_edges)")
        }
        render_fields = (
            ",e.layer,e.structure "
            if {"layer", "structure"}.issubset(edge_columns)
            else ",0 AS layer,0 AS structure "
        )
        edge_query = (
            "SELECT s.lat AS start_lat,s.lon AS start_lon,"
            "t.lat AS end_lat,t.lon AS end_lon,e.way_id,e.highway,e.name"
            + render_fields
            + "FROM route_nodes_rtree r "
            "JOIN route_nodes s ON s.id=r.id "
            "JOIN route_edges e ON e.source=s.id "
            "JOIN route_nodes t ON t.id=e.target "
            "WHERE r.min_lat BETWEEN ? AND ? AND r.min_lon BETWEEN ? AND ? "
            "{filter_sql} LIMIT ?"
        )
        major_filter = (
            "AND e.highway IN ('motorway','trunk','primary','secondary','tertiary') "
        )
        edge_rows: list[sqlite3.Row] = []
        feature_rows: list[sqlite3.Row] = []
        try:
            for latitude_index in range(grid_size):
                cell_min_lat = (
                    min_lat + (max_lat - min_lat) * latitude_index / grid_size
                )
                cell_max_lat = (
                    min_lat + (max_lat - min_lat) * (latitude_index + 1) / grid_size
                )
                for longitude_index in range(grid_size):
                    cell_min_lon = (
                        min_lon + (max_lon - min_lon) * longitude_index / grid_size
                    )
                    cell_max_lon = (
                        min_lon
                        + (max_lon - min_lon) * (longitude_index + 1) / grid_size
                    )
                    parameters = (
                        cell_min_lat,
                        cell_max_lat,
                        cell_min_lon,
                        cell_max_lon,
                    )
                    edge_rows.extend(
                        database.execute(
                            edge_query.format(filter_sql=major_filter),
                            (*parameters, major_per_cell),
                        ).fetchall()
                    )
                    edge_rows.extend(
                        database.execute(
                            edge_query.format(filter_sql=""),
                            (*parameters, detail_per_cell),
                        ).fetchall()
                    )
            feature_rows = database.execute(
                "SELECT f.osm_id,f.name,f.subcategory AS highway,f.geom,f.tags_json "
                "FROM features_rtree r JOIN features f ON f.id=r.id "
                "WHERE r.max_lat>=? AND r.min_lat<=? "
                "AND r.max_lon>=? AND r.min_lon<=? "
                "AND f.feature_type='road' AND f.geom IS NOT NULL "
                "ORDER BY CASE f.subcategory "
                "WHEN 'motorway' THEN 0 WHEN 'trunk' THEN 1 "
                "WHEN 'primary' THEN 2 WHEN 'secondary' THEN 3 "
                "WHEN 'tertiary' THEN 4 ELSE 5 END,f.osm_id LIMIT ?",
                (min_lat, max_lat, min_lon, max_lon, limit),
            ).fetchall()
        finally:
            database.close()
        render_levels: dict[int, tuple[int, int]] = {}
        for row in edge_rows:
            way_id = int(row["way_id"])
            candidate = (int(row["layer"] or 0), int(row["structure"] or 0))
            current = render_levels.get(way_id)
            if current is None or (candidate != (0, 0) and current == (0, 0)):
                render_levels[way_id] = candidate
        roads: list[RoadSegment] = []
        seen: set[tuple[object, ...]] = set()
        complete_way_ids: set[int] = set()
        for row in feature_rows:
            way_id = int(row["osm_id"])
            highway = str(row["highway"] or "road")
            name = _road_render_name(way_id, row["name"], row["tags_json"])
            layer, structure = _road_render_level(way_id, row["tags_json"])
            sampled_level = render_levels.get(way_id)
            if sampled_level is not None and sampled_level != (0, 0):
                layer, structure = sampled_level
            added = False
            try:
                geometry = _read_wkb(row["geom"])
                for raw_line in _iter_lines(geometry):
                    for raw_start, raw_end in zip(raw_line, raw_line[1:]):
                        start_point = (float(raw_start[1]), float(raw_start[0]))
                        end_point = (float(raw_end[1]), float(raw_end[0]))
                        if start_point == end_point:
                            continue
                        edge = tuple(sorted((start_point, end_point)))
                        key = (*edge, highway, name, layer, structure)
                        if key in seen:
                            continue
                        seen.add(key)
                        roads.append(
                            RoadSegment(
                                start=start_point,
                                end=end_point,
                                highway=highway,
                                name=name,
                                layer=layer,
                                structure=structure,
                            )
                        )
                        added = True
            except (ValueError, struct.error, TypeError, IndexError):
                continue
            if added:
                complete_way_ids.add(way_id)
        for row in edge_rows:
            way_id = int(row["way_id"])
            if way_id in complete_way_ids:
                continue
            start_point = (float(row["start_lat"]), float(row["start_lon"]))
            end_point = (float(row["end_lat"]), float(row["end_lon"]))
            highway = str(row["highway"] or "road")
            name = _road_name(way_id, row["name"])
            layer = int(row["layer"] or 0)
            structure = int(row["structure"] or 0)
            edge = tuple(sorted((start_point, end_point)))
            key = (*edge, highway, name, layer, structure)
            if key in seen:
                continue
            seen.add(key)
            roads.append(
                RoadSegment(
                    start=start_point,
                    end=end_point,
                    highway=highway,
                    name=name,
                    layer=layer,
                    structure=structure,
                )
            )
        return roads

    def _background_lines(
        self,
        bounds: tuple[float, float, float, float],
        *,
        limit: int = 4_000,
    ) -> list[BackgroundLine]:
        min_lat, min_lon, max_lat, max_lon = bounds
        if _haversine_m(min_lat, min_lon, max_lat, max_lon) > 80_000:
            return []
        database = self._connect()
        try:
            rows = database.execute(
                "SELECT f.name,f.category,f.subcategory,f.geom "
                "FROM features_rtree r JOIN features f ON f.id=r.id "
                "WHERE r.max_lat>=? AND r.min_lat<=? "
                "AND r.max_lon>=? AND r.min_lon<=? "
                "AND f.feature_type='line' AND f.geom IS NOT NULL "
                "ORDER BY CASE f.category "
                "WHEN 'waterway' THEN 0 WHEN 'railway' THEN 1 ELSE 2 END,f.id "
                "LIMIT ?",
                (min_lat, max_lat, min_lon, max_lon, limit),
            ).fetchall()
        finally:
            database.close()
        lines: list[BackgroundLine] = []
        for row in rows:
            try:
                geometry = _read_wkb(row["geom"])
                for raw_line in _iter_lines(geometry):
                    points = tuple(
                        (float(point[1]), float(point[0])) for point in raw_line
                    )
                    if len(points) >= 2:
                        lines.append(
                            BackgroundLine(
                                points=points,
                                category=str(row["category"] or ""),
                                subcategory=str(row["subcategory"] or ""),
                                name=str(row["name"] or ""),
                            )
                        )
            except (ValueError, struct.error, TypeError, IndexError):
                continue
        return lines

    def _background_pois(
        self,
        bounds: tuple[float, float, float, float],
        *,
        limit: int = 800,
    ) -> list[BackgroundPoi]:
        min_lat, min_lon, max_lat, max_lon = bounds
        if _haversine_m(min_lat, min_lon, max_lat, max_lon) > 80_000:
            return []
        database = self._connect()
        try:
            rows = database.execute(
                "SELECT f.name,f.category,f.subcategory,f.lat,f.lon "
                "FROM features_rtree r JOIN features f ON f.id=r.id "
                "WHERE r.max_lat>=? AND r.min_lat<=? "
                "AND r.max_lon>=? AND r.min_lon<=? "
                "AND f.lat BETWEEN ? AND ? AND f.lon BETWEEN ? AND ? "
                "AND f.feature_type IN ('poi','place') AND f.name<>'' "
                "ORDER BY f.id LIMIT ?",
                (
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                    min_lat,
                    max_lat,
                    min_lon,
                    max_lon,
                    limit,
                ),
            ).fetchall()
        finally:
            database.close()

        pois: list[BackgroundPoi] = []
        seen: set[tuple[str, float, float]] = set()
        for row in rows:
            category = str(row["category"] or "")
            subcategory = str(row["subcategory"] or "")
            priority = _poi_render_priority(category, subcategory)
            if priority is None:
                continue
            name = str(row["name"] or "").strip()
            latitude = float(row["lat"])
            longitude = float(row["lon"])
            identity = (name.casefold(), round(latitude, 4), round(longitude, 4))
            if not name or identity in seen:
                continue
            seen.add(identity)
            pois.append(
                BackgroundPoi(
                    latitude=latitude,
                    longitude=longitude,
                    name=name,
                    category=category,
                    subcategory=subcategory,
                    priority=priority,
                )
            )
        return sorted(pois, key=lambda poi: (poi.priority, poi.name))

    def _background_areas(
        self,
        bounds: tuple[float, float, float, float],
        *,
        limit: int = 1_500,
    ) -> list[MapPolygon]:
        min_lat, min_lon, max_lat, max_lon = bounds
        if _haversine_m(min_lat, min_lon, max_lat, max_lon) > 50_000:
            return []
        database = self._connect()
        try:
            has_render_layer = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='render_areas'"
            ).fetchone()
            if has_render_layer:
                rows = database.execute(
                    "SELECT a.category,a.subcategory,'' AS name,a.geom "
                    "FROM render_areas_rtree r JOIN render_areas a ON a.id=r.id "
                    "WHERE r.max_lat>=? AND r.min_lat<=? "
                    "AND r.max_lon>=? AND r.min_lon<=? "
                    "ORDER BY ((a.max_lat-a.min_lat)*(a.max_lon-a.min_lon)) DESC "
                    "LIMIT ?",
                    (min_lat, max_lat, min_lon, max_lon, limit),
                ).fetchall()
            else:
                rows = database.execute(
                    "SELECT f.category,f.subcategory,f.name,f.geom "
                    "FROM features_rtree r JOIN features f ON f.id=r.id "
                    "WHERE r.max_lat>=? AND r.min_lat<=? "
                    "AND r.max_lon>=? AND r.min_lon<=? "
                    "AND f.feature_type='area' AND f.geom IS NOT NULL "
                    "AND f.category IN ('building','landuse','natural','leisure','water','waterway') "
                    "ORDER BY ((f.max_lat-f.min_lat)*(f.max_lon-f.min_lon)) DESC "
                    "LIMIT ?",
                    (min_lat, max_lat, min_lon, max_lon, limit),
                ).fetchall()
        finally:
            database.close()

        areas: list[MapPolygon] = []
        for row in rows:
            try:
                geometry = _read_wkb(row["geom"])
                for raw_rings in _iter_polygons(geometry):
                    rings = tuple(
                        tuple((float(point[1]), float(point[0])) for point in raw_ring)
                        for raw_ring in raw_rings
                        if len(raw_ring) >= 3
                    )
                    if rings:
                        areas.append(
                            MapPolygon(
                                rings=rings,
                                category=str(row["category"] or ""),
                                subcategory=str(row["subcategory"] or ""),
                                name=str(row["name"] or ""),
                            )
                        )
                    if len(areas) >= limit:
                        return areas
            except (ValueError, struct.error, TypeError):
                continue
        return areas

    def _attach_visual(
        self,
        result: dict[str, Any],
        *,
        include_map_image: bool,
        title: str,
        subtitle: str,
        markers: Sequence[MapMarker],
        lines: Sequence[MapLine] = (),
        labels: Sequence[MapLabel] = (),
        circle: MapCircle | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        name: str = "地图.png",
    ) -> dict[str, Any]:
        root = self.settings.managed_file_root
        if not include_map_image or root is None:
            return result
        points = [(marker.latitude, marker.longitude) for marker in markers]
        for line in lines:
            points.extend(line.points)
        try:
            visible_bounds = fit_bounds_to_map(
                bounds or scene_bounds(points, circle=circle)
            )
            query_bounds = buffer_bounds(visible_bounds)
            descriptor = render_static_map(
                root,
                title=title,
                subtitle=subtitle,
                markers=markers,
                lines=lines,
                areas=self._background_areas(query_bounds),
                background_lines=self._background_lines(query_bounds),
                roads=self._background_roads(query_bounds),
                pois=self._background_pois(query_bounds),
                labels=labels,
                circle=circle,
                bounds=visible_bounds,
                name=name,
            )
        except Exception as exc:  # noqa: BLE001 - visualization is best effort
            logger.warning("Static map rendering failed (%s)", type(exc).__name__)
            return result
        result["visualization"] = {
            "type": "static_map",
            "caption": title,
            "media_type": "image/png",
        }
        result["managed_file"] = descriptor
        return result

    def dataset_info(self) -> dict[str, Any]:
        database = self._connect(require_ready=False)
        try:
            metadata = self._metadata(database)
        finally:
            database.close()
        counts = {
            key.removesuffix("_count"): int(value)
            for key, value in metadata.items()
            if key.endswith("_count") and value.isdigit()
        }
        routing = "ready" if counts.get("route_edge", 0) else "unavailable"
        return {
            "schema_version": metadata.get("schema_version", ""),
            "build_state": metadata.get("build_state", "unknown"),
            "source": {
                "path": metadata.get("source_name", ""),
                "replication_timestamp": metadata.get("replication_timestamp", ""),
                "replication_sequence": metadata.get("replication_sequence", ""),
                "pbf_checkpoint_sequence": metadata.get(
                    "source_checkpoint_sequence",
                    metadata.get("replication_sequence", ""),
                ),
                "sha256": metadata.get("source_sha256", ""),
            },
            "bounds_wgs84": json.loads(metadata.get("bounds", "{}")),
            "counts": counts,
            "capabilities": {
                "place_search": "ready",
                "reverse_geocoding": "ready",
                "nearby_search": "ready",
                "walking_routing": routing,
                "cycling_routing": routing,
                "driving_routing": routing,
                "public_transit_routing": "unsupported",
                "live_traffic": "unsupported",
                "weather": "unsupported",
            },
            "coordinate_systems": sorted(SUPPORTED_COORDINATE_SYSTEMS),
            "license": "OpenStreetMap contributors, ODbL 1.0",
        }

    def _road_override_rows(
        self,
        database: sqlite3.Connection,
        query: str,
        *,
        limit: int,
        feature_types: Sequence[str],
        bounds: tuple[float, float, float, float] | None,
    ) -> list[sqlite3.Row]:
        if feature_types and "road" not in feature_types:
            return []
        normalized_query = _normalize_text(query)
        override_ids = [
            way_id
            for way_id, name in _ROAD_NAME_OVERRIDES.items()
            if normalized_query in _normalize_text(name)
            or _normalize_text(name) in normalized_query
        ]
        if not override_ids:
            return []
        placeholders = ",".join("?" for _ in override_ids)
        sql = (
            "SELECT f.*,0.0 AS text_rank FROM features f "
            + ("JOIN features_rtree r ON r.id=f.id " if bounds is not None else "")
            + "WHERE f.osm_type='way' AND f.feature_type='road' "
            + f"AND f.osm_id IN ({placeholders}) "
        )
        parameters: list[Any] = list(override_ids)
        if bounds is not None:
            min_lat, min_lon, max_lat, max_lon = bounds
            sql += (
                "AND r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=? "
            )
            parameters.extend((min_lat, max_lat, min_lon, max_lon))
        return database.execute(
            sql + "LIMIT ?", [*parameters, min(500, max(1, limit))]
        ).fetchall()

    def _search_rows(
        self,
        database: sqlite3.Connection,
        query: str,
        *,
        limit: int,
        feature_types: Sequence[str] = (),
        bounds: tuple[float, float, float, float] | None = None,
        station_only: bool = False,
    ) -> list[sqlite3.Row]:
        override_rows = self._road_override_rows(
            database,
            query,
            limit=limit,
            feature_types=feature_types,
            bounds=bounds,
        )
        normalized_query = _normalize_text(query)
        if override_rows and any(
            normalized_query == _normalize_text(name)
            for name in _ROAD_NAME_OVERRIDES.values()
        ):
            return override_rows
        tokens = search_tokens(query)
        strict = _fts_expression(query)
        relaxed = None
        if len(tokens) > 2:
            # No-space queries like 上海人民广场 produce a spurious boundary
            # bigram (海人) that never appears in documents. Fall back to OR
            # so 上海 + 人民广场 still match.
            pieces = []
            for token in tokens[:32]:
                escaped = token.replace('"', '""')
                suffix = "*" if len(token) == 1 or token.isascii() else ""
                pieces.append(f'"{escaped}"{suffix}')
            relaxed = " OR ".join(pieces)
        sql_base = (
            "SELECT f.*,bm25(feature_fts) AS text_rank FROM feature_fts "
            "JOIN features f ON f.id=feature_fts.rowid "
        )
        parameters_base: list[Any] = []
        if bounds is not None:
            sql_base += "JOIN features_rtree r ON r.id=f.id "
        sql_base += "WHERE feature_fts MATCH ? "
        if station_only:
            sql_base += (
                "AND ((f.category='railway' AND f.subcategory IN "
                "('station','halt','tram_stop','subway_entrance')) OR "
                "(f.category='public_transport' AND f.subcategory='station') OR "
                "(f.category='amenity' AND f.subcategory='bus_station')) "
            )
        if feature_types:
            sql_base += "AND f.feature_type IN (%s) " % ",".join(
                "?" for _ in feature_types
            )
            parameters_base.extend(feature_types)
        if bounds is not None:
            min_lat, min_lon, max_lat, max_lon = bounds
            sql_base += (
                "AND r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=? "
            )
            parameters_base.extend((min_lat, max_lat, min_lon, max_lon))
        sql_base += "ORDER BY text_rank LIMIT ?"
        rows = database.execute(
            sql_base, [strict, *parameters_base, min(500, max(1, limit))]
        ).fetchall()
        if not rows and relaxed is not None:
            rows = database.execute(
                sql_base, [relaxed, *parameters_base, min(500, max(1, limit))]
            ).fetchall()
        seen_ids = {int(row["id"]) for row in rows}
        rows.extend(row for row in override_rows if int(row["id"]) not in seen_ids)
        return rows

    def _region_bounds(
        self,
        database: sqlite3.Connection,
        region: str,
    ) -> tuple[float, float, float, float] | None:
        rows = self._search_rows(
            database, region, limit=100, feature_types=("boundary", "place")
        )
        if not rows:
            return None
        scored: list[tuple[Any, ...]] = []
        for row in rows:
            bounds = (row["min_lat"], row["min_lon"], row["max_lat"], row["max_lon"])
            area = _bounds_area(bounds)
            scored.append(
                (
                    _region_name_score(row["name"], region),
                    0 if row["feature_type"] == "boundary" else 1,
                    int(row["admin_level"] or 99),
                    -area,
                    -int(row["population"] or 0),
                    bounds,
                )
            )
        scored.sort(key=lambda item: item[:-1])
        best_bounds = scored[0][5]
        # Safety net: never return a degenerate point from a tiny hamlet when a
        # large boundary with a decent name match exists.
        best_area = _bounds_area(best_bounds)
        if best_area < 1e-6:
            for item in scored:
                if item[0] <= 1 and _bounds_area(item[5]) > 1e-4:
                    return item[5]
            # Genuine point region (small town): expand so the RTree filter
            # does not collapse to zero results.
            min_lat, min_lon, max_lat, max_lon = best_bounds
            pad = 0.05
            return (min_lat - pad, min_lon - pad, max_lat + pad, max_lon + pad)
        return best_bounds

    def search_places(
        self,
        query: str,
        *,
        region: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
        coordinate_system: str = "wgs84",
        limit: int = 10,
        include_map_image: bool = False,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 200:
            raise MapError("invalid_query", "query must contain 1 to 200 characters")
        search_query = _location_search_query(query)
        station_query = _station_search_query(search_query)
        rank_query = search_query
        limit = max(1, min(int(limit), 30))
        if (latitude is None) != (longitude is None):
            raise MapError(
                "invalid_coordinate", "latitude and longitude must be supplied together"
            )
        bias = (
            convert_coordinate(latitude, longitude, coordinate_system, "wgs84")
            if latitude is not None and longitude is not None
            else None
        )
        database = self._connect()
        try:
            bounds = (
                self._region_bounds(database, region.strip())
                if region.strip()
                else None
            )
            if bounds is None and bias is not None:
                # Coordinate bias must affect retrieval, not just reranking:
                # nationwide names like 人民广场 have thousands of rows and
                # BM25 top-300 never contains the nearby one. Prefilter to a
                # ~60 km box first; fall back to global on empty.
                d_lat, d_lon = 0.3, 0.3 / max(0.2, math.cos(math.radians(bias[0])))
                near_bounds = (
                    bias[0] - d_lat,
                    bias[1] - d_lon,
                    bias[0] + d_lat,
                    bias[1] + d_lon,
                )
                nearby_rows: list[sqlite3.Row] = []
                global_rows: list[sqlite3.Row] = []
                if station_query:
                    nearby_rows = self._search_rows(
                        database,
                        station_query,
                        limit=300,
                        bounds=near_bounds,
                        station_only=True,
                    )
                    global_rows = self._search_rows(
                        database, station_query, limit=300, station_only=True
                    )
                    if nearby_rows or global_rows:
                        rank_query = station_query
                if not nearby_rows and not global_rows:
                    nearby_rows = self._search_rows(
                        database, search_query, limit=300, bounds=near_bounds
                    )
                    global_rows = self._search_rows(database, search_query, limit=300)
                seen_row_ids: set[int] = set()
                rows = []
                for row in (*nearby_rows, *global_rows):
                    row_id = int(row["id"])
                    if row_id in seen_row_ids:
                        continue
                    seen_row_ids.add(row_id)
                    rows.append(row)
            else:
                rows = []
                if station_query:
                    rows = self._search_rows(
                        database,
                        station_query,
                        limit=300,
                        bounds=bounds,
                        station_only=True,
                    )
                    if rows:
                        rank_query = station_query
                if not rows:
                    rows = self._search_rows(
                        database, search_query, limit=300, bounds=bounds
                    )
        finally:
            database.close()
        normalized = _normalize_text(rank_query)
        query_tokens = frozenset(search_tokens(rank_query))

        _PLACE_SUBRANK = {
            "city": 0,
            "town": 1,
            "borough": 2,
            "suburb": 3,
            "quarter": 4,
            "neighbourhood": 5,
            "neighborhood": 5,
            "subdistrict": 6,
            "village": 7,
            "hamlet": 8,
            "square": 9,
            "station": 1,
        }

        def rank(row: sqlite3.Row) -> tuple[Any, ...]:
            name = _normalize_text(_feature_name(row))
            aliases = {
                _normalize_text(value) for value in row["aliases"].split("|") if value
            }
            exact = _region_name_score(name, normalized) == 0 or any(
                _region_name_score(alias, normalized) == 0 for alias in aliases
            )
            prefix = name.startswith(normalized) or any(
                alias.startswith(normalized) for alias in aliases
            )
            document_tokens = frozenset(str(row["search_text"] or "").split())
            token_hits = len(query_tokens & document_tokens)
            distance = (
                _haversine_m(bias[0], bias[1], row["lat"], row["lon"])
                if bias is not None
                else 0.0
            )
            type_rank = {
                "place": 0,
                "poi": 1,
                "address": 2,
                "boundary": 3,
                "road": 4,
                "area": 5,
            }.get(row["feature_type"], 9)
            place_subrank = (
                _PLACE_SUBRANK.get(row["subcategory"], 4)
                if row["category"] == "place"
                else 0
            )
            tags = row["tags_json"]
            referenced = bool(
                row["website"] or '"wikidata"' in tags or '"wikipedia"' in tags
            )
            category, subcategory = _normalize_category(
                row["category"], row["subcategory"]
            )
            category_rank = {
                "place": 0,
                "tourism": 1,
                "historic": 1,
                "man_made": 1,
                "amenity": 2,
                "shop": 2,
                "leisure": 2,
                "railway": 2,
                "public_transport": (2 if subcategory == "station" else 4),
            }.get(category, 3)
            return (
                not exact,
                not prefix,
                -token_hits,
                -int(row["population"] or 0),
                place_subrank,
                not referenced,
                category_rank,
                type_rank,
                distance,
                float(row["text_rank"]),
            )

        rows.sort(key=rank)
        # Dedupe: one OSM object can be stored as poi+address or poi+area.
        # Keep the best-ranked feature_type per (osm_type, osm_id).
        seen: set[tuple[str, int]] = set()
        deduped: list[sqlite3.Row] = []
        for row in rows:
            key = (row["osm_type"], int(row["osm_id"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        selected = deduped[:limit]
        items = []
        for row in selected:
            distance = (
                _haversine_m(bias[0], bias[1], row["lat"], row["lon"])
                if bias is not None
                else None
            )
            items.append(
                _feature_result(
                    row, distance_m=distance, coordinate_system=coordinate_system
                )
            )
        result = {
            "query": query,
            "region": region,
            "results": items,
            "count": len(items),
        }
        markers = [
            MapMarker(
                float(row["lat"]),
                float(row["lon"]),
                str(index),
                "#7c3aed",
            )
            for index, row in enumerate(selected, start=1)
        ]
        if bias is not None:
            markers.insert(0, MapMarker(bias[0], bias[1], "我", "#2563eb"))
        return self._attach_visual(
            result,
            include_map_image=include_map_image,
            title=f"地点搜索：{query}",
            subtitle=f"{region + ' · ' if region else ''}{len(items)} 个结果",
            markers=markers,
            name="地点搜索地图.png",
        )

    def nearby_search(
        self,
        latitude: float,
        longitude: float,
        *,
        coordinate_system: str = "wgs84",
        radius_m: int = 1_000,
        query: str = "",
        categories: Sequence[str] = (),
        limit: int = 20,
        include_map_image: bool = False,
    ) -> dict[str, Any]:
        wgs_lat, wgs_lon = convert_coordinate(
            latitude, longitude, coordinate_system, "wgs84"
        )
        radius_m, limit = (
            max(10, min(int(radius_m), 50_000)),
            max(1, min(int(limit), 50)),
        )
        d_lat = radius_m / 110_540
        d_lon = radius_m / max(10_000, 111_320 * math.cos(math.radians(wgs_lat)))
        parameters: list[Any] = [
            wgs_lat - d_lat,
            wgs_lat + d_lat,
            wgs_lon - d_lon,
            wgs_lon + d_lon,
        ]
        if query.strip():
            sql = (
                "SELECT f.* FROM feature_fts JOIN features f ON f.id=feature_fts.rowid "
                "JOIN features_rtree r ON r.id=f.id WHERE r.max_lat>=? AND r.min_lat<=? "
                "AND r.max_lon>=? AND r.min_lon<=? AND feature_fts MATCH ? "
            )
            parameters.append(_fts_expression(query))
        else:
            sql = (
                "SELECT f.* FROM features f JOIN features_rtree r ON r.id=f.id "
                "WHERE r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=? "
                "AND f.feature_type IN ('poi','place','address') "
            )
        values = [value.strip() for value in categories if value.strip()][:20]
        normalized_values: list[str] = []
        for value in values:
            if ":" in value:
                category, subcategory = value.split(":", 1)
                category, subcategory = _normalize_category(
                    category.strip(), subcategory.strip()
                )
                normalized_values.append(f"{category}:{subcategory}")
            else:
                normalized_values.append(value.strip())
        values = normalized_values
        if values:
            clauses = []
            for value in values:
                if ":" in value:
                    category, subcategory = value.split(":", 1)
                    clauses.append("(f.category=? AND f.subcategory=?)")
                    parameters.extend((category, subcategory))
                else:
                    clauses.append("(f.category=? OR f.subcategory=?)")
                    parameters.extend((value, value))
            sql += "AND (" + " OR ".join(clauses) + ") "
        sql += "LIMIT 1000"
        database = self._connect()
        try:
            rows = database.execute(sql, parameters).fetchall()
        finally:
            database.close()
        candidates = []
        for row in rows:
            distance = _haversine_m(wgs_lat, wgs_lon, row["lat"], row["lon"])
            if distance <= radius_m:
                candidates.append((distance, row))
        candidates.sort(key=lambda item: (item[0], item[1]["name"]))
        seen_keys: set[tuple[str, int]] = set()
        results = []
        visual_rows: list[sqlite3.Row] = []
        for distance, row in candidates:
            key = (row["osm_type"], int(row["osm_id"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(
                _feature_result(
                    row, distance_m=distance, coordinate_system=coordinate_system
                )
            )
            visual_rows.append(row)
            if len(results) >= limit:
                break
        result = {
            "center": {
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_system": coordinate_system,
            },
            "radius_m": radius_m,
            "results": results,
            "count": len(results),
        }
        markers = [MapMarker(wgs_lat, wgs_lon, "我", "#2563eb")]
        markers.extend(
            MapMarker(
                float(row["lat"]),
                float(row["lon"]),
                str(index),
                "#f97316",
            )
            for index, row in enumerate(visual_rows, start=1)
        )
        return self._attach_visual(
            result,
            include_map_image=include_map_image,
            title=f"周边查找{f'：{query}' if query.strip() else ''}",
            subtitle=f"半径{_format_distance_zh(radius_m)} · {len(results)} 个结果",
            markers=markers,
            circle=MapCircle(wgs_lat, wgs_lon, radius_m),
            name="周边查找地图.png",
        )

    def reverse_geocode(
        self,
        latitude: float,
        longitude: float,
        *,
        coordinate_system: str = "wgs84",
        nearby_limit: int = 5,
        include_map_image: bool = False,
    ) -> dict[str, Any]:
        wgs_lat, wgs_lon = convert_coordinate(
            latitude, longitude, coordinate_system, "wgs84"
        )
        nearby_limit = max(0, min(int(nearby_limit), 10))
        database = self._connect()
        try:
            boundaries = database.execute(
                "SELECT f.* FROM features f JOIN features_rtree r ON r.id=f.id "
                "WHERE f.feature_type='boundary' AND r.min_lat<=? AND r.max_lat>=? "
                "AND r.min_lon<=? AND r.max_lon>=?",
                (wgs_lat, wgs_lat, wgs_lon, wgs_lon),
            ).fetchall()
            span_lat = 2_000 / 110_540
            span_lon = 2_000 / max(10_000, 111_320 * math.cos(math.radians(wgs_lat)))
            nearby_rows = database.execute(
                "SELECT f.* FROM features f JOIN features_rtree r ON r.id=f.id "
                "WHERE f.feature_type IN ('address','road','poi','place') "
                "AND r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=? LIMIT 500",
                (
                    wgs_lat - span_lat,
                    wgs_lat + span_lat,
                    wgs_lon - span_lon,
                    wgs_lon + span_lon,
                ),
            ).fetchall()
        finally:
            database.close()
        containing = []
        for row in boundaries:
            try:
                if _geometry_contains(_read_wkb(row["geom"]), wgs_lon, wgs_lat):
                    containing.append(row)
            except (ValueError, struct.error):
                continue
        containing.sort(key=lambda row: int(row["admin_level"] or 99))
        nearby = []
        for row in nearby_rows:
            try:
                distance = _distance_to_geometry_m(
                    wgs_lat, wgs_lon, _read_wkb(row["geom"])
                )
            except (ValueError, struct.error):
                distance = None
            if distance is None:
                distance = _haversine_m(wgs_lat, wgs_lon, row["lat"], row["lon"])
            if distance <= 2_000:
                nearby.append((distance, row))
        nearby.sort(key=lambda item: (item[0], item[1]["feature_type"] != "address"))
        deduped_nearby: list[tuple[float, sqlite3.Row]] = []
        seen_nearby: set[tuple[str, int]] = set()
        for distance, row in nearby:
            key = (row["osm_type"], int(row["osm_id"]))
            if key in seen_nearby:
                continue
            seen_nearby.add(key)
            deduped_nearby.append((distance, row))
        nearby = deduped_nearby
        administrative = [
            {
                "name": row["name"],
                "admin_level": int(row["admin_level"]),
                "place_id": _place_id(row),
            }
            for row in containing
        ]
        parts = [item["name"] for item in administrative]
        if nearby:
            label = nearby[0][1]["address"] or nearby[0][1]["name"]
            if label and label not in parts:
                parts.append(label)
        selected_nearby = nearby[:nearby_limit]
        result = {
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_system": coordinate_system,
            },
            "wgs84": {"latitude": round(wgs_lat, 7), "longitude": round(wgs_lon, 7)},
            "formatted_address": "".join(parts),
            "administrative_areas": administrative,
            "nearby": [
                _feature_result(
                    row, distance_m=distance, coordinate_system=coordinate_system
                )
                for distance, row in selected_nearby
            ],
        }
        markers = [MapMarker(wgs_lat, wgs_lon, "我", "#2563eb")]
        markers.extend(
            MapMarker(
                float(row["lat"]),
                float(row["lon"]),
                str(index),
                "#f97316",
            )
            for index, (_distance, row) in enumerate(selected_nearby, start=1)
        )
        return self._attach_visual(
            result,
            include_map_image=include_map_image,
            title="当前位置",
            subtitle=result["formatted_address"]
            or f"附近 {len(selected_nearby)} 个地点",
            markers=markers,
            circle=MapCircle(wgs_lat, wgs_lon, 2_000),
            name="当前位置地图.png",
        )

    def get_place(
        self,
        place_id: str,
        *,
        coordinate_system: str = "wgs84",
        include_map_image: bool = False,
    ) -> dict[str, Any]:
        match = re.fullmatch(
            r"osm:(node|way|relation|area):(-?\d+):([a-z_]+)", place_id
        )
        if match is None:
            raise MapError(
                "invalid_place_id", "place_id is not a valid OSM map identifier"
            )
        database = self._connect()
        try:
            row = database.execute(
                "SELECT * FROM features WHERE osm_type=? AND osm_id=? AND feature_type=?",
                (match.group(1), int(match.group(2)), match.group(3)),
            ).fetchone()
        finally:
            database.close()
        if row is None:
            raise MapError("place_not_found", "place_id was not found in this dataset")
        result = _feature_result(row, coordinate_system=coordinate_system, details=True)
        shape_lines: list[MapLine] = []
        try:
            geometry = _read_wkb(row["geom"])
            for raw_line in _iter_lines(geometry):
                points = [(float(point[1]), float(point[0])) for point in raw_line]
                if len(points) > 1_200:
                    stride = math.ceil(len(points) / 1_200)
                    points = points[::stride]
                    endpoint = (float(raw_line[-1][1]), float(raw_line[-1][0]))
                    if points[-1] != endpoint:
                        points.append(endpoint)
                if len(points) >= 2:
                    shape_lines.append(
                        MapLine(tuple(points), color="#7c3aed", width=5, outline=True)
                    )
                if len(shape_lines) >= 20:
                    break
        except (ValueError, struct.error, TypeError):
            shape_lines = []
        bounds = scene_bounds(
            [
                (float(row["min_lat"]), float(row["min_lon"])),
                (float(row["max_lat"]), float(row["max_lon"])),
            ]
        )
        return self._attach_visual(
            result,
            include_map_image=include_map_image,
            title=str(row["name"] or "地点详情"),
            subtitle=f"{result['category']}:{result['subcategory']}",
            markers=[MapMarker(float(row["lat"]), float(row["lon"]), "1", "#7c3aed")],
            lines=shape_lines,
            bounds=bounds,
            name="地点详情地图.png",
        )

    def _resolve_candidates(
        self,
        value: str,
        *,
        coordinate_system: str,
        limit: int = 6,
        bias_latitude: float | None = None,
        bias_longitude: float | None = None,
    ) -> list[tuple[float, float, dict[str, Any]]]:
        """Resolve text to ranked coordinate candidates (never just top-1)."""
        cleaned = value.strip()
        coordinate = _COORDINATE_RE.search(cleaned)
        if coordinate is not None:
            latitude, longitude = convert_coordinate(
                float(coordinate.group(1)),
                float(coordinate.group(2)),
                coordinate_system,
                "wgs84",
            )
            return [(latitude, longitude, {"input": value, "matched_by": "coordinate"})]
        kwargs: dict[str, Any] = {}
        if bias_latitude is not None and bias_longitude is not None:
            kwargs = {
                "latitude": bias_latitude,
                "longitude": bias_longitude,
                "coordinate_system": coordinate_system,
            }
        search_queries = _location_search_queries(cleaned)
        station_intent = len(search_queries) > 1
        result_groups = []
        for query in search_queries:
            result = self.search_places(query, limit=limit, **kwargs)
            result_groups.append(result)
            if station_intent and any(
                (place.get("category") == "railway")
                or (
                    place.get("category") == "public_transport"
                    and place.get("subcategory") == "station"
                )
                for place in result["results"]
            ):
                break
        search_queries = search_queries[: len(result_groups)]
        if not any(result["results"] for result in result_groups):
            raise MapError(
                "location_not_found", f"location could not be resolved: {cleaned}"
            )
        ranked: list[tuple[int, int, int, tuple[float, float, dict[str, Any]]]] = []
        seen_places: set[str] = set()
        for query_index, (resolved_query, result) in enumerate(
            zip(search_queries, result_groups, strict=True)
        ):
            for result_index, place in enumerate(result["results"]):
                place_id = str(place.get("place_id", ""))
                if place_id in seen_places:
                    continue
                seen_places.add(place_id)
                location = place["location"]
                category = str(place.get("category", ""))
                subcategory = str(place.get("subcategory", ""))
                station_rank = 0
                if station_intent:
                    if category == "railway" and subcategory in {
                        "station",
                        "halt",
                        "tram_stop",
                        "subway_entrance",
                    }:
                        station_rank = 0
                    elif category == "public_transport" and subcategory == "station":
                        station_rank = 1
                    elif category == "amenity" and subcategory == "bus_station":
                        station_rank = 2
                    elif category == "public_transport" and subcategory in {
                        "stop_position",
                        "platform",
                    }:
                        station_rank = 3
                    else:
                        station_rank = 4
                candidate = (
                    float(location["latitude"]),
                    float(location["longitude"]),
                    {
                        "input": value,
                        "matched_by": "place",
                        "resolved_query": resolved_query,
                        "place": place,
                    },
                )
                ranked.append((station_rank, query_index, result_index, candidate))
        ranked.sort(key=lambda item: item[:3])
        return [item[3] for item in ranked[:limit]]

    def _resolve_location(
        self,
        value: str,
        *,
        coordinate_system: str,
    ) -> tuple[float, float, dict[str, Any]]:
        return self._resolve_candidates(
            value, coordinate_system=coordinate_system, limit=1
        )[0]

    def _nearest_route_node(
        self,
        database: sqlite3.Connection,
        latitude: float,
        longitude: float,
        mode_bit: int,
    ) -> tuple[int, float, float, float]:
        for radius_m in (200, 500, 1_000, 3_000, 10_000):
            d_lat = radius_m / 110_540
            d_lon = radius_m / max(10_000, 111_320 * math.cos(math.radians(latitude)))
            rows = database.execute(
                "SELECT n.id,n.lat,n.lon FROM route_nodes n JOIN route_nodes_rtree r ON r.id=n.id "
                "WHERE (n.modes & ?) != 0 AND r.max_lat>=? AND r.min_lat<=? "
                "AND r.max_lon>=? AND r.min_lon<=? LIMIT 500",
                (
                    mode_bit,
                    latitude - d_lat,
                    latitude + d_lat,
                    longitude - d_lon,
                    longitude + d_lon,
                ),
            ).fetchall()
            if rows:
                best = min(
                    rows,
                    key=lambda row: _haversine_m(
                        latitude, longitude, row["lat"], row["lon"]
                    ),
                )
                distance = _haversine_m(latitude, longitude, best["lat"], best["lon"])
                return int(best["id"]), float(best["lat"]), float(best["lon"]), distance
        raise MapError(
            "route_network_not_found", "no routable road was found near the location"
        )

    @staticmethod
    def _edge_speed_mps(mode: str, highway: str, speed_kph: float) -> float:
        if mode == "walking":
            return 0.75 if highway == "steps" else 1.33
        if mode == "cycling":
            return {"cycleway": 5.6, "path": 4.2, "track": 3.5, "steps": 0.8}.get(
                highway, 4.5
            )
        effective = speed_kph or {
            "motorway": 100,
            "trunk": 80,
            "primary": 60,
            "secondary": 50,
            "tertiary": 40,
            "residential": 30,
            "living_street": 15,
            "service": 20,
        }.get(highway, 30)
        return max(2.0, min(effective, 130.0) / 3.6)

    def _route_search(
        self,
        database: sqlite3.Connection,
        start: int,
        goal: int,
        goal_lat: float,
        goal_lon: float,
        mode: str,
    ) -> tuple[list[int], list[dict[str, Any]], float, float, int]:
        if start == goal:
            return [start], [], 0.0, 0.0, 0
        mode_bit = MODE_BITS[mode]
        max_speed = {"walking": 1.6, "cycling": 9.0, "driving": 36.2}[mode]
        deadline = time.monotonic() + self.settings.route_timeout_seconds
        queue: list[tuple[float, float, int]] = [(0.0, 0.0, start)]
        best_cost = {start: 0.0}
        predecessor: dict[int, tuple[int, dict[str, Any]]] = {}
        closed: set[int] = set()
        expansions = 0
        sql = (
            "SELECT e.target AS neighbor,e.length_m,e.name,e.highway,e.speed_kph,"
            "e.way_id,e.seq,n.lat AS neighbor_lat,n.lon AS neighbor_lon "
            "FROM route_edges e JOIN route_nodes n ON n.id=e.target "
            "WHERE e.source=? AND (e.forward_modes & ?) != 0 UNION ALL "
            "SELECT e.source AS neighbor,e.length_m,e.name,e.highway,e.speed_kph,"
            "e.way_id,e.seq,n.lat AS neighbor_lat,n.lon AS neighbor_lon "
            "FROM route_edges e JOIN route_nodes n ON n.id=e.source "
            "WHERE e.target=? AND (e.backward_modes & ?) != 0"
        )
        while queue:
            _, cost, node = heapq.heappop(queue)
            if node in closed or cost != best_cost.get(node):
                continue
            if node == goal:
                break
            closed.add(node)
            expansions += 1
            if (
                expansions > self.settings.route_max_expansions
                or time.monotonic() > deadline
            ):
                raise MapError(
                    "route_search_limit",
                    "route search reached its bounded resource limit",
                )
            for edge in database.execute(sql, (node, mode_bit, node, mode_bit)):
                neighbor = int(edge["neighbor"])
                if neighbor in closed:
                    continue
                speed = self._edge_speed_mps(
                    mode, edge["highway"], float(edge["speed_kph"] or 0)
                )
                candidate = cost + float(edge["length_m"]) / speed
                if candidate >= best_cost.get(neighbor, math.inf):
                    continue
                heuristic = (
                    _haversine_m(
                        edge["neighbor_lat"],
                        edge["neighbor_lon"],
                        goal_lat,
                        goal_lon,
                    )
                    / max_speed
                )
                best_cost[neighbor] = candidate
                predecessor[neighbor] = (node, dict(edge))
                heapq.heappush(queue, (candidate + heuristic, candidate, neighbor))
        if goal not in predecessor:
            raise MapError(
                "route_not_found", "the locations are not connected for this mode"
            )
        nodes, edges, current = [goal], [], goal
        while current != start:
            previous, edge = predecessor[current]
            nodes.append(previous)
            edges.append(edge)
            current = previous
        nodes.reverse()
        edges.reverse()
        for edge in edges:
            edge["name"] = _road_name(int(edge["way_id"]), edge.get("name"))
        distance = sum(float(edge["length_m"]) for edge in edges)
        return nodes, edges, distance, best_cost[goal], expansions

    @staticmethod
    def _route_steps(
        edges: Sequence[dict[str, Any]],
        *,
        start_latitude: float,
        start_longitude: float,
    ) -> list[str]:
        if not edges:
            return ["已到达目的地"]
        groups: list[dict[str, Any]] = []
        latitude, longitude = start_latitude, start_longitude
        for edge in edges:
            next_latitude = float(edge["neighbor_lat"])
            next_longitude = float(edge["neighbor_lon"])
            raw_name = str(edge.get("name") or "").strip()
            highway = str(edge.get("highway") or "road")
            label = raw_name or _HIGHWAY_LABELS.get(highway, "道路")
            bearing = _bearing_degrees(
                latitude,
                longitude,
                next_latitude,
                next_longitude,
            )
            key = (raw_name, "" if raw_name else highway)
            if groups and groups[-1]["key"] == key:
                groups[-1]["distance_m"] += float(edge["length_m"])
                groups[-1]["end"] = (next_latitude, next_longitude)
                groups[-1]["end_bearing"] = bearing
            else:
                groups.append(
                    {
                        "key": key,
                        "name": label,
                        "highway": highway,
                        "distance_m": float(edge["length_m"]),
                        "start": (latitude, longitude),
                        "end": (next_latitude, next_longitude),
                        "start_bearing": bearing,
                        "end_bearing": bearing,
                    }
                )
            latitude, longitude = next_latitude, next_longitude
        if len(groups) > 30:
            middle = groups[14:-14]
            groups = [
                *groups[:14],
                {
                    "key": ("规划路线", "mixed"),
                    "name": "规划路线",
                    "highway": "mixed",
                    "distance_m": sum(item["distance_m"] for item in middle),
                    "start": middle[0]["start"],
                    "end": middle[-1]["end"],
                    "start_bearing": middle[0]["start_bearing"],
                    "end_bearing": middle[-1]["end_bearing"],
                },
                *groups[-14:],
            ]
        steps: list[str] = []
        for index, group in enumerate(groups):
            direction = _compass_direction(group["start_bearing"])
            distance_text = _format_distance_zh(group["distance_m"])
            if index == 0:
                maneuver, prefix = "depart", f"出发，向{direction}沿{group['name']}"
            else:
                maneuver, directive = _turn_directive(
                    groups[index - 1]["end_bearing"],
                    group["start_bearing"],
                )
                if maneuver == "continue":
                    prefix = f"{directive}，沿{group['name']}向{direction}"
                else:
                    prefix = f"{directive}进入{group['name']}，向{direction}"
            instruction = f"{prefix}行进{distance_text}"
            if index == len(groups) - 1:
                instruction += "，随后到达目的地"
            steps.append(instruction)
        return steps

    @staticmethod
    def _route_step_points(
        edges: Sequence[dict[str, Any]],
        *,
        start_latitude: float,
        start_longitude: float,
    ) -> list[tuple[float, float]]:
        groups: list[tuple[tuple[str, str], tuple[float, float]]] = []
        latitude, longitude = start_latitude, start_longitude
        for edge in edges:
            raw_name = str(edge.get("name") or "").strip()
            highway = str(edge.get("highway") or "road")
            key = (raw_name, "" if raw_name else highway)
            if not groups or groups[-1][0] != key:
                groups.append((key, (latitude, longitude)))
            latitude = float(edge["neighbor_lat"])
            longitude = float(edge["neighbor_lon"])
        if len(groups) > 30:
            groups = [*groups[:14], groups[14], *groups[-14:]]
        return [point for _key, point in groups]

    @staticmethod
    def _route_map_labels(
        edges: Sequence[dict[str, Any]],
        *,
        start_latitude: float,
        start_longitude: float,
        limit: int = 12,
    ) -> list[MapLabel]:
        """Place labels at the distance midpoint of named route sections."""
        groups: list[dict[str, Any]] = []
        latitude, longitude = start_latitude, start_longitude
        for edge in edges:
            next_latitude = float(edge["neighbor_lat"])
            next_longitude = float(edge["neighbor_lon"])
            name = str(edge.get("name") or "").strip()
            if name:
                segment = (
                    latitude,
                    longitude,
                    next_latitude,
                    next_longitude,
                    max(0.0, float(edge["length_m"])),
                )
                if groups and groups[-1]["name"] == name:
                    groups[-1]["segments"].append(segment)
                    groups[-1]["distance_m"] += segment[-1]
                else:
                    groups.append(
                        {
                            "name": name,
                            "highway": str(edge.get("highway") or "road"),
                            "distance_m": segment[-1],
                            "segments": [segment],
                        }
                    )
            latitude, longitude = next_latitude, next_longitude

        road_rank = {
            "motorway": 0,
            "trunk": 1,
            "primary": 2,
            "secondary": 3,
            "tertiary": 4,
            "unclassified": 5,
            "residential": 6,
            "living_street": 7,
            "service": 8,
        }
        candidates: list[tuple[int, float, int, MapLabel]] = []
        for index, group in enumerate(groups):
            midpoint_distance = group["distance_m"] / 2
            traversed = 0.0
            anchor = None
            for (
                start_lat,
                start_lon,
                end_lat,
                end_lon,
                segment_distance,
            ) in group["segments"]:
                if traversed + segment_distance >= midpoint_distance:
                    ratio = (
                        (midpoint_distance - traversed) / segment_distance
                        if segment_distance
                        else 0.5
                    )
                    anchor = (
                        start_lat + (end_lat - start_lat) * ratio,
                        start_lon + (end_lon - start_lon) * ratio,
                    )
                    break
                traversed += segment_distance
            if anchor is None:
                segment = group["segments"][-1]
                anchor = (segment[2], segment[3])
            candidates.append(
                (
                    road_rank.get(group["highway"], 8),
                    -group["distance_m"],
                    index,
                    MapLabel(anchor[0], anchor[1], group["name"]),
                )
            )

        labels: list[MapLabel] = []
        seen: set[str] = set()
        for _rank, _distance, _index, label in sorted(candidates):
            if label.text in seen:
                continue
            seen.add(label.text)
            labels.append(label)
            if len(labels) >= limit:
                break
        return labels

    def route(
        self,
        origin: str,
        destination: str,
        *,
        mode: str = "driving",
        coordinate_system: str = "wgs84",
        output_coordinate_system: str = "wgs84",
        include_geometry: bool = False,
        include_map_image: bool = False,
    ) -> dict[str, Any]:
        if mode not in MODE_BITS:
            raise MapError(
                "invalid_route_mode", "route mode must be walking, cycling or driving"
            )
        origin_cands = self._resolve_candidates(
            origin, coordinate_system=coordinate_system, limit=6
        )
        dest_cands = self._resolve_candidates(
            destination, coordinate_system=coordinate_system, limit=6
        )
        # Mutual geographic bias: ambiguous names like 人民广场 exist in many
        # cities. Bias dest resolution toward the origin top-1 and vice versa
        # so same-city pairs surface even when unbiased top-6 misses them.
        try:
            o0_lat, o0_lon, _ = origin_cands[0]
            biased_dest = self._resolve_candidates(
                destination,
                coordinate_system=coordinate_system,
                limit=6,
                bias_latitude=o0_lat,
                bias_longitude=o0_lon,
            )
        except (MapError, IndexError):
            biased_dest = []
        try:
            d0_lat, d0_lon, _ = dest_cands[0]
            biased_origin = self._resolve_candidates(
                origin,
                coordinate_system=coordinate_system,
                limit=6,
                bias_latitude=d0_lat,
                bias_longitude=d0_lon,
            )
        except (MapError, IndexError):
            biased_origin = []
        seen_places: set[str] = set()
        merged_origin: list[tuple[float, float, dict[str, Any]]] = []
        for cand in (*biased_origin, *origin_cands):
            pid = str(cand[2].get("place", {}).get("place_id", cand[:2]))
            if pid in seen_places:
                continue
            seen_places.add(pid)
            merged_origin.append(cand)
        seen_places.clear()
        merged_dest: list[tuple[float, float, dict[str, Any]]] = []
        for cand in (*biased_dest, *dest_cands):
            pid = str(cand[2].get("place", {}).get("place_id", cand[:2]))
            if pid in seen_places:
                continue
            seen_places.add(pid)
            merged_dest.append(cand)
        # Rank pairs by candidate rank first, distance second: a far同名
        # (e.g. 故宫北院区 25 km away) must not beat the intended top-1 pair.
        # Only nearby fallbacks (same complex gates) are worth trying.
        indexed_pairs = []
        for i, (o_lat, o_lon, o_match) in enumerate(merged_origin[:6]):
            for j, (d_lat, d_lon, d_match) in enumerate(merged_dest[:6]):
                if i + j > 4:
                    continue
                indexed_pairs.append(
                    (
                        i + j,
                        _haversine_m(o_lat, o_lon, d_lat, d_lon),
                        o_lat,
                        o_lon,
                        o_match,
                        d_lat,
                        d_lon,
                        d_match,
                    )
                )
        indexed_pairs.sort(key=lambda p: (p[0], p[1]))
        pairs = [p[2:] for p in indexed_pairs]
        best_straight = indexed_pairs[0][1] if indexed_pairs else 0.0
        database = self._connect()
        last_error: MapError | None = None
        try:
            mode_bit = MODE_BITS[mode]
            for (
                origin_lat,
                origin_lon,
                origin_match,
                dest_lat,
                dest_lon,
                dest_match,
            ) in pairs:
                straight = _haversine_m(origin_lat, origin_lon, dest_lat, dest_lon)
                # Don't silently route from a far同名 fallback (e.g. 故宫北院区
                # 25 km away) when the intended pair is next door but car-free.
                if straight > best_straight + 5_000 and straight > 10_000:
                    continue
                # Bounded A* cannot serve inter-province driving; fail fast
                # with a clear code instead of burning the expansion budget.
                if straight > 300_000:
                    last_error = MapError(
                        "route_too_far",
                        "locations are too far apart for offline bounded routing",
                    )
                    continue
                try:
                    start, start_lat, start_lon, start_distance = (
                        self._nearest_route_node(
                            database, origin_lat, origin_lon, mode_bit
                        )
                    )
                    goal, goal_lat, goal_lon, goal_distance = self._nearest_route_node(
                        database, dest_lat, dest_lon, mode_bit
                    )
                except MapError as exc:
                    last_error = exc
                    continue
                # Skip pairs whose road snap is absurd (e.g. wrong-city match).
                if start_distance > 5_000 or goal_distance > 5_000:
                    last_error = MapError(
                        "route_network_not_found",
                        "no routable road was found near the resolved place",
                    )
                    continue
                try:
                    _node_ids, edges, distance, duration, expansions = (
                        self._route_search(
                            database, start, goal, goal_lat, goal_lon, mode
                        )
                    )
                except MapError as exc:
                    last_error = exc
                    # route_not_found on this pair -> try next candidate pair
                    # (e.g. 故宫 area centroid is car-free, museum gate is not).
                    continue
                break
            else:
                raise last_error or MapError(
                    "route_not_found", "the locations are not connected for this mode"
                )
        except sqlite3.OperationalError as exc:
            if "route_" in str(exc) or "no such table" in str(exc):
                raise MapError(
                    "route_unavailable", "this map database has no routing index"
                ) from exc
            raise
        finally:
            database.close()
        points = [(start_lat, start_lon)]
        points.extend(
            (float(edge["neighbor_lat"]), float(edge["neighbor_lon"])) for edge in edges
        )
        origin_label = _route_match_label(origin_match, "起点")
        destination_label = _route_match_label(dest_match, "目的地")
        distance_text = _format_distance_zh(distance)
        duration_text = _format_duration_zh(duration)
        steps = self._route_steps(
            edges,
            start_latitude=start_lat,
            start_longitude=start_lon,
        )
        result = {
            "mode": mode,
            "origin": {**origin_match, "snap_distance_m": round(start_distance)},
            "destination": {**dest_match, "snap_distance_m": round(goal_distance)},
            "distance_m": round(distance),
            "duration_seconds": round(duration),
            "summary": {
                "text": f"{_MODE_LABELS[mode]}{distance_text}，预计{duration_text}",
                "mode_label": _MODE_LABELS[mode],
                "distance": distance_text,
                "duration": duration_text,
            },
            "steps": steps,
            "route_preview": _route_preview(
                points,
                distance,
                output_coordinate_system,
            ),
            "navigation": {
                "provider": "amap",
                "label": "打开高德地图导航",
                "url": _amap_navigation_url(
                    origin_lat,
                    origin_lon,
                    dest_lat,
                    dest_lon,
                    origin_name=origin_label,
                    destination_name=destination_label,
                    mode=mode,
                ),
                "coordinate_system": "gcj02",
                "note": "打开后由高德地图按实时路况重新规划路线",
            },
            "search": {"expanded_nodes": expansions},
        }
        if include_geometry:
            simplified = _douglas_peucker(points, max(2.0, distance / 50_000))
            if len(simplified) > 500:
                stride = math.ceil(len(simplified) / 500)
                simplified = simplified[::stride]
                if simplified[-1] != points[-1]:
                    simplified.append(points[-1])
            geometry = []
            for latitude, longitude in simplified:
                converted_lat, converted_lon = convert_coordinate(
                    latitude, longitude, "wgs84", output_coordinate_system
                )
                geometry.append([round(converted_lon, 7), round(converted_lat, 7)])
            result["geometry"] = {
                "type": "LineString",
                "coordinates": geometry,
                "coordinate_system": output_coordinate_system,
            }
        image_points = _douglas_peucker(points, max(2.0, distance / 100_000))
        if len(image_points) > 2_000:
            stride = math.ceil(len(image_points) / 2_000)
            image_points = image_points[::stride]
            if image_points[-1] != points[-1]:
                image_points.append(points[-1])
        step_points = self._route_step_points(
            edges,
            start_latitude=start_lat,
            start_longitude=start_lon,
        )
        route_labels = self._route_map_labels(
            edges,
            start_latitude=start_lat,
            start_longitude=start_lon,
        )
        numbered_points = list(enumerate(step_points[1:], start=2))
        if len(numbered_points) > 12:
            numbered_points = [*numbered_points[:6], *numbered_points[-6:]]
        markers = [
            *(
                MapMarker(latitude, longitude, str(index), "#f59e0b")
                for index, (latitude, longitude) in numbered_points
            ),
            MapMarker(points[0][0], points[0][1], "起", "#16a34a"),
            MapMarker(points[-1][0], points[-1][1], "终", "#dc2626"),
        ]
        return self._attach_visual(
            result,
            include_map_image=include_map_image,
            title=f"{_MODE_LABELS[mode]}路线",
            subtitle=result["summary"]["text"],
            markers=markers,
            lines=[
                MapLine(tuple(image_points), color="#1677ff", width=7, outline=True)
            ],
            labels=route_labels,
            name="路线图.png",
        )

    def distance(
        self,
        origins: Sequence[str],
        destination: str,
        *,
        mode: str = "straight",
        coordinate_system: str = "wgs84",
        include_map_image: bool = False,
    ) -> dict[str, Any]:
        if not origins or len(origins) > 16:
            raise MapError("invalid_origins", "origins must contain 1 to 16 locations")
        dest_lat, dest_lon, dest_match = self._resolve_location(
            destination, coordinate_system=coordinate_system
        )
        results = []
        visual_markers = [MapMarker(dest_lat, dest_lon, "终", "#dc2626")]
        visual_lines: list[MapLine] = []
        colors = ("#2563eb", "#7c3aed", "#0891b2", "#ea580c")
        if mode == "straight":
            for index, origin in enumerate(origins, start=1):
                latitude, longitude, match = self._resolve_location(
                    origin, coordinate_system=coordinate_system
                )
                results.append(
                    {
                        "origin": match,
                        "distance_m": round(
                            _haversine_m(latitude, longitude, dest_lat, dest_lon)
                        ),
                    }
                )
                color = colors[(index - 1) % len(colors)]
                visual_markers.append(MapMarker(latitude, longitude, str(index), color))
                visual_lines.append(
                    MapLine(
                        ((latitude, longitude), (dest_lat, dest_lon)),
                        color=color,
                        width=4,
                        outline=True,
                    )
                )
        elif mode in MODE_BITS:
            if len(origins) > 4:
                raise MapError(
                    "route_matrix_too_large", "road distance accepts at most 4 origins"
                )
            for index, origin in enumerate(origins, start=1):
                routed = self.route(
                    origin,
                    destination,
                    mode=mode,
                    coordinate_system=coordinate_system,
                    include_map_image=False,
                )
                results.append(
                    {
                        "origin": routed["origin"],
                        "distance_m": routed["distance_m"],
                        "duration_seconds": routed["duration_seconds"],
                    }
                )
                preview = routed["route_preview"]["coordinates"]
                route_points = tuple(
                    (float(point[1]), float(point[0])) for point in preview
                )
                if route_points:
                    color = colors[(index - 1) % len(colors)]
                    visual_markers.append(
                        MapMarker(
                            route_points[0][0], route_points[0][1], str(index), color
                        )
                    )
                    visual_lines.append(
                        MapLine(route_points, color=color, width=5, outline=True)
                    )
        else:
            raise MapError(
                "invalid_distance_mode",
                "distance mode must be straight, walking, cycling or driving",
            )
        result = {"mode": mode, "destination": dest_match, "results": results}
        mode_label = "直线" if mode == "straight" else _MODE_LABELS[mode]
        return self._attach_visual(
            result,
            include_map_image=include_map_image,
            title="距离比较",
            subtitle=f"{mode_label}距离 · {len(results)} 个起点",
            markers=visual_markers,
            lines=visual_lines,
            name="距离比较地图.png",
        )

    def categories(self) -> dict[str, Any]:
        return {"categories": category_inventory()}
