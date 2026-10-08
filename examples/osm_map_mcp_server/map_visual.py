"""Small offline map renderer for spatial MCP results."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import time
from typing import Iterable, Sequence
import uuid


_OUTPUT_WIDTH = 1600
_OUTPUT_HEIGHT = 1000
_HEADER_HEIGHT = 112
_MAP_MARGIN = 48
_PLOT_ASPECT_RATIO = (_OUTPUT_WIDTH - _MAP_MARGIN * 2) / (
    _OUTPUT_HEIGHT - _HEADER_HEIGHT - _MAP_MARGIN
)


@dataclass(frozen=True)
class MapMarker:
    latitude: float
    longitude: float
    label: str
    color: str = "#2563eb"


@dataclass(frozen=True)
class MapLine:
    points: tuple[tuple[float, float], ...]
    color: str = "#1677ff"
    width: int = 6
    outline: bool = False


@dataclass(frozen=True)
class BackgroundLine:
    points: tuple[tuple[float, float], ...]
    category: str
    subcategory: str = ""
    name: str = ""


@dataclass(frozen=True)
class MapPolygon:
    rings: tuple[tuple[tuple[float, float], ...], ...]
    category: str
    subcategory: str = ""
    name: str = ""


@dataclass(frozen=True)
class RoadSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    highway: str
    name: str = ""
    layer: int = 0
    structure: int = 0


@dataclass(frozen=True)
class MapLabel:
    latitude: float
    longitude: float
    text: str


@dataclass(frozen=True)
class MapCircle:
    latitude: float
    longitude: float
    radius_m: float


def scene_bounds(
    points: Iterable[tuple[float, float]],
    *,
    circle: MapCircle | None = None,
) -> tuple[float, float, float, float]:
    """Return padded WGS84 bounds for the visible map scene."""

    valid = [
        (float(latitude), float(longitude))
        for latitude, longitude in points
        if math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ]
    if circle is not None:
        d_lat = circle.radius_m / 110_540
        d_lon = circle.radius_m / max(
            10_000,
            111_320 * math.cos(math.radians(circle.latitude)),
        )
        valid.extend(
            (
                (circle.latitude - d_lat, circle.longitude - d_lon),
                (circle.latitude + d_lat, circle.longitude + d_lon),
            )
        )
    if not valid:
        raise ValueError("a map scene requires at least one valid coordinate")
    min_lat = min(point[0] for point in valid)
    max_lat = max(point[0] for point in valid)
    min_lon = min(point[1] for point in valid)
    max_lon = max(point[1] for point in valid)
    center_lat = (min_lat + max_lat) / 2
    min_lat_span = 800 / 110_540
    min_lon_span = 800 / max(
        10_000,
        111_320 * math.cos(math.radians(center_lat)),
    )
    lat_span = max(max_lat - min_lat, min_lat_span)
    lon_span = max(max_lon - min_lon, min_lon_span)
    lat_pad = lat_span * 0.12
    lon_pad = lon_span * 0.12
    return (
        max(-85.0, min_lat - lat_pad),
        max(-180.0, min_lon - lon_pad),
        min(85.0, max_lat + lat_pad),
        min(180.0, max_lon + lon_pad),
    )


def _mercator(latitude: float, longitude: float) -> tuple[float, float]:
    latitude = max(-85.0, min(85.0, latitude))
    y = math.degrees(math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2)))
    return longitude, y


def _inverse_mercator_y(value: float) -> float:
    return math.degrees(math.atan(math.sinh(math.radians(value))))


def buffer_bounds(
    bounds: tuple[float, float, float, float],
    *,
    fraction: float = 0.08,
) -> tuple[float, float, float, float]:
    """Expand map bounds on every side for continuous background queries."""

    min_lat, min_lon, max_lat, max_lon = bounds
    min_x, min_y = _mercator(min_lat, min_lon)
    max_x, max_y = _mercator(max_lat, max_lon)
    padding = max(0.0, float(fraction))
    x_padding = max(1e-9, max_x - min_x) * padding
    y_padding = max(1e-9, max_y - min_y) * padding
    return (
        max(-85.0, _inverse_mercator_y(min_y - y_padding)),
        max(-180.0, min_x - x_padding),
        min(85.0, _inverse_mercator_y(max_y + y_padding)),
        min(180.0, max_x + x_padding),
    )


def fit_bounds_to_map(
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Expand bounds to the final map aspect ratio in Web Mercator space."""

    min_lat, min_lon, max_lat, max_lon = bounds
    min_x, min_y = _mercator(min_lat, min_lon)
    max_x, max_y = _mercator(max_lat, max_lon)
    span_x = max(1e-9, max_x - min_x)
    span_y = max(1e-9, max_y - min_y)
    if span_x / span_y < _PLOT_ASPECT_RATIO:
        expansion = span_y * _PLOT_ASPECT_RATIO - span_x
        min_x -= expansion / 2
        max_x += expansion / 2
    else:
        expansion = span_x / _PLOT_ASPECT_RATIO - span_y
        min_y -= expansion / 2
        max_y += expansion / 2

    return (
        max(-85.0, min(min_lat, _inverse_mercator_y(min_y))),
        max(-180.0, min(min_lon, min_x)),
        min(85.0, max(max_lat, _inverse_mercator_y(max_y))),
        min(180.0, max(max_lon, max_x)),
    )


class _Viewport:
    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        *,
        width: int,
        height: int,
        top: int,
        margin: int,
    ) -> None:
        min_lat, min_lon, max_lat, max_lon = bounds
        min_x, min_y = _mercator(min_lat, min_lon)
        max_x, max_y = _mercator(max_lat, max_lon)
        span_x = max(1e-9, max_x - min_x)
        span_y = max(1e-9, max_y - min_y)
        plot_width = width - margin * 2
        plot_height = height - top - margin
        target_ratio = plot_width / plot_height
        if span_x / span_y < target_ratio:
            expansion = span_y * target_ratio - span_x
            min_x -= expansion / 2
            max_x += expansion / 2
        else:
            expansion = span_x / target_ratio - span_y
            min_y -= expansion / 2
            max_y += expansion / 2
        self.min_x = min_x
        self.max_y = max_y
        self.scale = min(
            plot_width / (max_x - min_x),
            plot_height / (max_y - min_y),
        )
        self.margin = margin
        self.top = top

    def zoom(self, supersampling: int = 1) -> float:
        """Return the equivalent Web Mercator zoom for output pixels."""

        output_pixels_per_degree = self.scale / max(1, supersampling)
        return math.log2(output_pixels_per_degree * 360 / 256)

    def point(self, latitude: float, longitude: float) -> tuple[int, int]:
        x, y = _mercator(latitude, longitude)
        return (
            round(self.margin + (x - self.min_x) * self.scale),
            round(self.top + (self.max_y - y) * self.scale),
        )


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    candidates = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


_ROAD_RANK = {
    "motorway": 0,
    "trunk": 1,
    "primary": 2,
    "secondary": 3,
    "tertiary": 4,
    "unclassified": 5,
    "residential": 6,
    "living_street": 7,
    "service": 8,
    "pedestrian": 9,
    "footway": 10,
    "path": 11,
    "steps": 12,
}


def _road_rank(highway: str) -> int:
    return _ROAD_RANK.get(highway, 8)


def _interpolate_width(
    zoom: float,
    stops: Sequence[tuple[float, float]],
) -> int:
    if zoom <= stops[0][0]:
        return max(1, math.floor(stops[0][1] + 0.5))
    for (start_zoom, start_width), (end_zoom, end_width) in zip(stops, stops[1:]):
        if zoom <= end_zoom:
            ratio = (zoom - start_zoom) / (end_zoom - start_zoom)
            width = start_width + (end_width - start_width) * ratio
            return max(1, math.floor(width + 0.5))
    return max(1, math.floor(stops[-1][1] + 0.5))


_ROAD_WIDTH_STOPS = {
    "motorway": ((12, 2.5), (14, 4), (16, 6), (18, 9)),
    "trunk": ((12, 2), (14, 3), (16, 5), (18, 8)),
    "primary": ((12, 1.5), (14, 2.5), (16, 4.5), (18, 7)),
    "secondary": ((13, 1.5), (14, 2), (16, 3.5), (18, 6)),
    "tertiary": ((14, 1), (16, 2.5), (18, 5)),
    "minor": ((15, 1), (16, 1.5), (18, 3)),
    "path": ((16, 1), (18, 2)),
}


def _road_style(
    highway: str,
    *,
    zoom: float | None = None,
) -> tuple[str, str, int]:
    width_key = highway
    if highway in {"footway", "path", "pedestrian", "steps"}:
        width_key = "path"
    elif highway not in _ROAD_WIDTH_STOPS:
        width_key = "minor"
    width = (
        _interpolate_width(zoom, _ROAD_WIDTH_STOPS[width_key])
        if zoom is not None
        else None
    )
    if highway == "motorway":
        return "#f5b35d", "#d98935", width or 9
    if highway == "trunk":
        return "#f7c879", "#dca553", width or 8
    if highway == "primary":
        return "#f8dda0", "#d3b56e", width or 7
    if highway == "secondary":
        return "#fffdf8", "#c8c1b6", width or 6
    if highway == "tertiary":
        return "#fffefb", "#d6d0c7", width or 5
    if highway in {"footway", "path", "pedestrian", "steps"}:
        return "#f6f1e8", "#d9d2c7", width or 2
    return "#fffefd", "#ddd8d0", width or 3


def _road_level(road: RoadSegment) -> tuple[int, int]:
    if road.structure < 0:
        return 0, road.layer
    if road.structure > 0:
        return 2, road.layer
    return 1, road.layer


def _road_paint(
    road: RoadSegment,
    *,
    zoom: float | None,
) -> tuple[str, str, int]:
    fill, casing, width = _road_style(road.highway, zoom=zoom)
    if road.structure < 0:
        fill = "#ffead1" if road.highway == "motorway" else "#fff4c6"
        casing = "#d8c5a0"
    return fill, casing, width


def _draw_roads(
    draw, viewport: _Viewport, roads: Sequence[RoadSegment], scale: int
) -> None:
    """Draw roads by class so segment casings cannot cover joined road surfaces."""

    zoom = viewport.zoom(scale) if hasattr(viewport, "zoom") else None
    roads_by_rank: dict[tuple[int, int, int], list[RoadSegment]] = {}
    for road in roads:
        level, layer = _road_level(road)
        key = (level, layer, _road_rank(road.highway))
        roads_by_rank.setdefault(key, []).append(road)

    # Tunnels go below surface roads and bridges go above them. Within one
    # level, less important roads go down first. Every casing is completed
    # before its surfaces so adjacent segments join without dark seams.
    for key in sorted(
        roads_by_rank,
        key=lambda item: (item[0], item[1], -item[2]),
    ):
        ranked_roads = roads_by_rank[key]
        for road in ranked_roads:
            start = viewport.point(*road.start)
            end = viewport.point(*road.end)
            _fill, casing, road_width = _road_paint(road, zoom=zoom)
            draw.line(
                (start, end),
                fill=casing,
                width=(road_width + 2) * scale,
            )
        for road in ranked_roads:
            start = viewport.point(*road.start)
            end = viewport.point(*road.end)
            fill, _casing, road_width = _road_paint(road, zoom=zoom)
            draw.line(
                (start, end),
                fill=fill,
                width=road_width * scale,
            )


def _background_line_style(
    category: str,
    subcategory: str,
    *,
    zoom: float | None,
) -> tuple[str, str, int]:
    if category == "waterway":
        width_key = (
            subcategory
            if subcategory in {"river", "canal", "stream", "drain", "ditch"}
            else "stream"
        )
        stops = {
            "river": ((11, 1.5), (14, 2.5), (16, 4), (18, 7)),
            "canal": ((12, 1), (14, 2), (16, 3.5), (18, 6)),
            "stream": ((13, 1), (16, 2), (18, 4)),
            "drain": ((14, 1), (16, 1.5), (18, 3)),
            "ditch": ((14, 1), (16, 1.5), (18, 3)),
        }[width_key]
        width = _interpolate_width(zoom, stops) if zoom is not None else 2
        return "#9fd8eb", "#d9eef6", width
    if category == "railway":
        width = (
            _interpolate_width(zoom, ((12, 1), (16, 2), (18, 3)))
            if zoom is not None
            else 2
        )
        return "#aaa39c", "#f4f1ed", width
    return "#aeb8bc", "#eef1f2", 1


def _draw_background_lines(
    draw,
    viewport: _Viewport,
    lines: Sequence[BackgroundLine],
    scale: int,
) -> None:
    zoom = viewport.zoom(scale) if hasattr(viewport, "zoom") else None
    for line in lines:
        if len(line.points) < 2:
            continue
        stride = max(1, math.ceil(len(line.points) / 2_000))
        sampled = list(line.points[::stride])
        if sampled[-1] != line.points[-1]:
            sampled.append(line.points[-1])
        pixels = [viewport.point(*point) for point in sampled]
        fill, casing, width = _background_line_style(
            line.category, line.subcategory, zoom=zoom
        )
        draw.line(
            pixels,
            fill=casing,
            width=(width + 2) * scale,
            joint="curve",
        )
        draw.line(
            pixels,
            fill=fill,
            width=width * scale,
            joint="curve",
        )


def _area_style(category: str, subcategory: str) -> tuple[int, str, str]:
    """Return draw order, fill and outline for an OSM area."""

    if (
        category in {"water", "waterway"}
        or (category == "natural" and subcategory in {"water", "bay", "wetland"})
        or (category == "landuse" and subcategory in {"reservoir", "basin"})
    ):
        return 40, "#c9e7f4", "#9fcbdc"
    if category == "leisure" and subcategory in {
        "park",
        "garden",
        "nature_reserve",
        "golf_course",
        "pitch",
    }:
        return 30, "#dcefd4", "#bfdcaf"
    if category == "natural" and subcategory in {
        "wood",
        "grassland",
        "scrub",
        "heath",
    }:
        return 25, "#d9ebce", "#bfd8ae"
    if category == "landuse" and subcategory in {
        "forest",
        "grass",
        "meadow",
        "orchard",
        "farmland",
        "village_green",
    }:
        return 25, "#e2edd2", "#ccdcb9"
    if category == "landuse" and subcategory in {
        "industrial",
        "commercial",
        "retail",
        "railway",
    }:
        return 20, "#e9e2ea", "#d7cbd9"
    if category == "landuse" and subcategory in {"residential", "construction"}:
        return 15, "#eeeae5", "#ded8d0"
    if category == "building":
        return 50, "#ded9d2", "#c6beb4"
    return 10, "#ebe9e3", "#d9d5cd"


def _draw_areas(
    draw, viewport: _Viewport, areas: Sequence[MapPolygon], scale: int
) -> None:
    for area in sorted(
        areas,
        key=lambda item: _area_style(item.category, item.subcategory)[0],
    ):
        _order, fill, outline = _area_style(area.category, area.subcategory)
        if not area.rings:
            continue
        pixel_rings: list[list[tuple[int, int]]] = []
        for ring in area.rings:
            if len(ring) < 3:
                continue
            stride = max(1, math.ceil(len(ring) / 1_200))
            pixels = [viewport.point(*point) for point in ring[::stride]]
            endpoint = viewport.point(*ring[-1])
            if pixels[-1] != endpoint:
                pixels.append(endpoint)
            if len(pixels) >= 3:
                pixel_rings.append(pixels)
        if not pixel_rings:
            continue
        draw.polygon(pixel_rings[0], fill=fill, outline=outline, width=scale)
        # Pillow has no direct even-odd fill. Clearing inner rings is a close
        # approximation for the uncommon holes in these background layers.
        for hole in pixel_rings[1:]:
            draw.polygon(hole, fill="#f7f6f2", outline=outline, width=scale)


def _draw_centered_text(draw, xy, text: str, font, fill: str, scale: int) -> None:
    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        anchor="mm",
        stroke_width=2 * scale,
        stroke_fill="#ffffff",
    )


def _label_box(
    draw,
    xy,
    text: str,
    font,
    *,
    scale: int,
    stroke_width: int = 0,
):
    left, top, right, bottom = draw.textbbox(
        xy,
        text,
        font=font,
        anchor="mm",
        stroke_width=stroke_width,
    )
    return (
        left - 5 * scale,
        top - 3 * scale,
        right + 5 * scale,
        bottom + 3 * scale,
    )


def _boxes_overlap(first, second, *, gap: int = 4) -> bool:
    return not (
        first[2] + gap < second[0]
        or second[2] + gap < first[0]
        or first[3] + gap < second[1]
        or second[3] + gap < first[1]
    )


def _draw_route_labels(
    draw,
    viewport: _Viewport,
    labels: Sequence[MapLabel],
    font,
    *,
    width: int,
    height: int,
    header: int,
    margin: int,
    scale: int,
) -> list[tuple[int, int, int, int]]:
    occupied: list[tuple[int, int, int, int]] = []
    offsets = tuple(
        (x * scale, y * scale)
        for x, y in (
            (0, -23),
            (23, 0),
            (-23, 0),
            (0, 23),
            (32, -22),
            (-32, -22),
        )
    )
    seen: set[str] = set()
    for label in labels:
        text = label.text.strip()[:16]
        if not text or text in seen:
            continue
        seen.add(text)
        origin_x, origin_y = viewport.point(label.latitude, label.longitude)
        selected = None
        for offset_x, offset_y in offsets:
            xy = (origin_x + offset_x, origin_y + offset_y)
            box = _label_box(draw, xy, text, font, scale=scale, stroke_width=scale)
            inside = (
                margin <= box[0]
                and box[2] <= width - margin
                and header + 4 <= box[1]
                and box[3] <= height - margin
            )
            if inside and not any(
                _boxes_overlap(box, item, gap=4 * scale) for item in occupied
            ):
                selected = (xy, box)
                break
        if selected is None:
            continue
        xy, box = selected
        draw.rounded_rectangle(
            box,
            radius=6 * scale,
            fill="#ffffff",
            outline="#60a5fa",
            width=scale,
        )
        draw.text(xy, text, font=font, fill="#0f3b68", anchor="mm")
        occupied.append(box)
    return occupied


def _draw_background_labels(
    draw,
    viewport: _Viewport,
    roads: Sequence[RoadSegment],
    font,
    *,
    width: int,
    height: int,
    header: int,
    margin: int,
    excluded_names: set[str],
    occupied: list[tuple[int, int, int, int]],
    scale: int,
) -> None:
    grouped: dict[str, list[RoadSegment]] = {}
    for road in roads:
        name = road.name.strip()
        if (
            not name
            or name in excluded_names
            or _road_rank(road.highway) > _road_rank("tertiary")
        ):
            continue
        grouped.setdefault(name, []).append(road)

    center = ((width + margin) / 2, (header + height - margin) / 2)
    candidates: list[tuple[float, str, tuple[tuple[int, int], ...]]] = []
    importance = {0: 125.0, 1: 118.0, 2: 110.0, 3: 82.0, 4: 62.0}
    for name, segments in grouped.items():
        rendered = []
        best_rank = min(_road_rank(segment.highway) for segment in segments)
        for segment in segments:
            start = viewport.point(*segment.start)
            end = viewport.point(*segment.end)
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            midpoint = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
            distance = math.hypot(midpoint[0] - center[0], midpoint[1] - center[1])
            rendered.append((length, distance, midpoint))
        total_length = sum(item[0] for item in rendered)
        if total_length < 18 * scale:
            continue
        ranked_anchors = sorted(
            rendered,
            key=lambda item: item[0] * 2 - item[1] * 0.12,
            reverse=True,
        )
        anchor = ranked_anchors[0]
        score = (
            importance.get(best_rank, 0)
            + min(total_length / scale, 500) * 0.12
            - (anchor[1] / scale) * 0.08
        )
        candidates.append(
            (score, name[:16], tuple(item[2] for item in ranked_anchors[:12]))
        )

    for _score, text, anchors in sorted(
        candidates, key=lambda item: item[0], reverse=True
    )[:32]:
        selected = None
        for xy in anchors:
            box = _label_box(
                draw,
                xy,
                text,
                font,
                scale=scale,
                stroke_width=3 * scale,
            )
            inside = (
                margin <= box[0]
                and box[2] <= width - margin
                and header + 4 <= box[1]
                and box[3] <= height - margin
            )
            if inside and not any(
                _boxes_overlap(box, item, gap=8 * scale) for item in occupied
            ):
                selected = (xy, box)
                break
        if selected is None:
            continue
        xy, box = selected
        _draw_centered_text(draw, xy, text, font, "#334155", scale)
        occupied.append(box)
        if len(occupied) >= len(excluded_names) + 18:
            break


def render_static_map(
    root: Path,
    *,
    title: str,
    subtitle: str,
    markers: Sequence[MapMarker],
    lines: Sequence[MapLine] = (),
    areas: Sequence[MapPolygon] = (),
    background_lines: Sequence[BackgroundLine] = (),
    roads: Sequence[RoadSegment] = (),
    labels: Sequence[MapLabel] = (),
    circle: MapCircle | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    name: str = "地图.png",
) -> dict[str, object]:
    """Render one bounded PNG and return a Knoa managed-file descriptor."""

    from PIL import Image, ImageDraw

    all_points = [(marker.latitude, marker.longitude) for marker in markers]
    for line in lines:
        all_points.extend(line.points)
    if bounds is None:
        bounds = scene_bounds(all_points, circle=circle)

    output_width, output_height = _OUTPUT_WIDTH, _OUTPUT_HEIGHT
    scale = 2
    width, height = output_width * scale, output_height * scale
    header, margin = _HEADER_HEIGHT * scale, _MAP_MARGIN * scale
    viewport = _Viewport(
        bounds,
        width=width,
        height=height,
        top=header,
        margin=margin,
    )
    image = Image.new("RGB", (width, height), "#f7f6f2")
    draw = ImageDraw.Draw(image)
    title_font = _font(34 * scale, bold=True)
    subtitle_font = _font(20 * scale)
    label_font = _font(17 * scale)
    route_label_font = _font(17 * scale, bold=True)
    marker_font = _font(18 * scale, bold=True)

    _draw_areas(draw, viewport, areas, scale)

    _draw_background_lines(draw, viewport, background_lines, scale)

    _draw_roads(draw, viewport, roads, scale)

    draw.rectangle((0, 0, width, header), fill="#ffffff")
    draw.line(
        (0, header - scale, width, header - scale),
        fill="#d7d2ca",
        width=scale,
    )
    draw.text((margin, 16 * scale), title[:64], font=title_font, fill="#172033")
    draw.text((margin, 65 * scale), subtitle[:96], font=subtitle_font, fill="#526071")

    if circle is not None:
        d_lat = circle.radius_m / 110_540
        d_lon = circle.radius_m / max(
            10_000,
            111_320 * math.cos(math.radians(circle.latitude)),
        )
        left, bottom = viewport.point(
            circle.latitude - d_lat,
            circle.longitude - d_lon,
        )
        right, top = viewport.point(
            circle.latitude + d_lat,
            circle.longitude + d_lon,
        )
        draw.ellipse(
            (left, top, right, bottom),
            outline="#3b82f6",
            width=3 * scale,
        )

    for line in lines:
        if len(line.points) < 2:
            continue
        pixels = [viewport.point(*point) for point in line.points]
        if line.outline:
            draw.line(
                pixels,
                fill="#ffffff",
                width=(line.width + 6) * scale,
                joint="curve",
            )
        draw.line(
            pixels,
            fill=line.color,
            width=line.width * scale,
            joint="curve",
        )

    excluded_names = {label.text.strip() for label in labels if label.text.strip()}
    occupied_labels = _draw_route_labels(
        draw,
        viewport,
        labels,
        route_label_font,
        width=width,
        height=height,
        header=header,
        margin=margin,
        scale=scale,
    )
    _draw_background_labels(
        draw,
        viewport,
        roads,
        label_font,
        width=width,
        height=height,
        header=header,
        margin=margin,
        excluded_names=excluded_names,
        occupied=occupied_labels,
        scale=scale,
    )

    occupied_markers: list[tuple[int, int, int]] = []
    marker_offsets = (
        (0, 0),
        (0, -46),
        (46, 0),
        (0, 46),
        (-46, 0),
        (36, -36),
        (36, 36),
        (-36, 36),
        (-36, -36),
        (0, -80),
        (80, 0),
        (0, 80),
        (-80, 0),
        (60, -60),
        (60, 60),
        (-60, 60),
        (-60, -60),
    )
    marker_offsets = tuple((x * scale, y * scale) for x, y in marker_offsets)
    for marker in markers:
        origin_x, origin_y = viewport.point(marker.latitude, marker.longitude)
        radius = (17 if len(marker.label) <= 2 else 14) * scale
        x, y = origin_x, origin_y
        for offset_x, offset_y in marker_offsets:
            candidate_x = origin_x + offset_x
            candidate_y = origin_y + offset_y
            inside_plot = (
                margin + radius <= candidate_x <= width - margin - radius
                and header + radius <= candidate_y <= height - margin - radius
            )
            clear = all(
                radius + other_radius + 6 * scale
                <= math.hypot(candidate_x - other_x, candidate_y - other_y)
                for other_x, other_y, other_radius in occupied_markers
            )
            marker_box = (
                candidate_x - radius - 4 * scale,
                candidate_y - radius - 4 * scale,
                candidate_x + radius + 4 * scale,
                candidate_y + radius + 4 * scale,
            )
            clear_labels = not any(
                _boxes_overlap(marker_box, label_box, gap=5 * scale)
                for label_box in occupied_labels
            )
            if inside_plot and clear and clear_labels:
                x, y = candidate_x, candidate_y
                break
        if (x, y) != (origin_x, origin_y):
            draw.line(
                (origin_x, origin_y, x, y),
                fill=marker.color,
                width=2 * scale,
            )
        draw.ellipse(
            (
                x - radius - 3 * scale,
                y - radius - 3 * scale,
                x + radius + 3 * scale,
                y + radius + 3 * scale,
            ),
            fill="#ffffff",
        )
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=marker.color,
            outline="#ffffff",
            width=2 * scale,
        )
        if marker.label:
            draw.text(
                (x, y - scale),
                marker.label[:3],
                font=marker_font,
                fill="#ffffff",
                anchor="mm",
            )
        occupied_markers.append((x, y, radius))

    draw.polygon(
        (
            (width - 58 * scale, header + 12 * scale),
            (width - 67 * scale, header + 36 * scale),
            (width - 49 * scale, header + 36 * scale),
        ),
        fill="#344054",
    )
    draw.text(
        (width - 58 * scale, header + 51 * scale),
        "N",
        font=label_font,
        fill="#344054",
        anchor="mm",
    )
    draw.text(
        (width - margin, height - 20 * scale),
        "© OpenStreetMap contributors",
        font=_font(14 * scale),
        fill="#64748b",
        anchor="ra",
    )

    image = image.resize((output_width, output_height), Image.Resampling.LANCZOS)

    root = root.expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = time.time()
    for stale in root.glob("osm-map-*.png"):
        try:
            if now - stale.stat().st_mtime > 3_600:
                stale.unlink()
        except OSError:
            continue
    filename = f"osm-map-{uuid.uuid4().hex}.png"
    destination = root / filename
    temporary = root / f".{filename}.tmp"
    image.save(temporary, format="PNG", optimize=True)
    temporary.chmod(0o600)
    temporary.replace(destination)
    data = destination.read_bytes()
    return {
        "kind": "managed_file",
        "relative_handle": filename,
        "name": name,
        "media_type": "image/png",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
