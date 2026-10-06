"""Small offline map renderer for spatial MCP results."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import time
from typing import Iterable, Sequence
import uuid


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
class RoadSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    highway: str
    name: str = ""


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
    """Return padded WGS84 bounds suitable for querying background roads."""

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


def _road_style(highway: str) -> tuple[str, int]:
    if highway in {"motorway", "trunk", "primary"}:
        return "#d3b891", 6
    if highway in {"secondary", "tertiary"}:
        return "#d5cec0", 4
    if highway in {"footway", "path", "pedestrian", "steps"}:
        return "#d5d2c9", 2
    return "#dedbd3", 3


def _draw_centered_text(draw, xy, text: str, font, fill: str) -> None:
    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        anchor="mm",
        stroke_width=2,
        stroke_fill="#ffffff",
    )


def render_static_map(
    root: Path,
    *,
    title: str,
    subtitle: str,
    markers: Sequence[MapMarker],
    lines: Sequence[MapLine] = (),
    roads: Sequence[RoadSegment] = (),
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

    width, height, header, margin = 960, 600, 82, 38
    viewport = _Viewport(
        bounds,
        width=width,
        height=height,
        top=header,
        margin=margin,
    )
    image = Image.new("RGB", (width, height), "#f5f4ef")
    draw = ImageDraw.Draw(image)
    title_font = _font(26, bold=True)
    subtitle_font = _font(17)
    label_font = _font(15)
    marker_font = _font(17, bold=True)

    for index in range(1, 5):
        x = margin + (width - margin * 2) * index / 5
        y = header + (height - header - margin) * index / 5
        draw.line((x, header, x, height - margin), fill="#ebe9e2", width=1)
        draw.line((margin, y, width - margin, y), fill="#ebe9e2", width=1)

    for road in roads:
        start = viewport.point(*road.start)
        end = viewport.point(*road.end)
        color, road_width = _road_style(road.highway)
        draw.line((start, end), fill="#ffffff", width=road_width + 3)
        draw.line((start, end), fill=color, width=road_width)

    labels_seen: set[str] = set()
    label_count = 0
    for road in roads:
        road_name = road.name.strip()
        if (
            not road_name
            or road_name in labels_seen
            or road.highway
            not in {"motorway", "trunk", "primary", "secondary", "tertiary"}
        ):
            continue
        labels_seen.add(road_name)
        midpoint = (
            (road.start[0] + road.end[0]) / 2,
            (road.start[1] + road.end[1]) / 2,
        )
        _draw_centered_text(
            draw,
            viewport.point(*midpoint),
            road_name[:12],
            label_font,
            "#7a746b",
        )
        label_count += 1
        if label_count >= 10:
            break

    draw.rectangle((0, 0, width, header), fill="#ffffff")
    draw.line((0, header - 1, width, header - 1), fill="#dedbd3", width=1)
    draw.text((margin, 16), title[:64], font=title_font, fill="#172033")
    draw.text((margin, 51), subtitle[:96], font=subtitle_font, fill="#5c667a")

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
            outline="#4b91f1",
            width=3,
        )

    for line in lines:
        if len(line.points) < 2:
            continue
        pixels = [viewport.point(*point) for point in line.points]
        if line.outline:
            draw.line(pixels, fill="#ffffff", width=line.width + 6, joint="curve")
        draw.line(pixels, fill=line.color, width=line.width, joint="curve")

    occupied_markers: list[tuple[int, int, int]] = []
    marker_offsets = (
        (0, 0),
        (0, -38),
        (38, 0),
        (0, 38),
        (-38, 0),
        (30, -30),
        (30, 30),
        (-30, 30),
        (-30, -30),
        (0, -68),
        (68, 0),
        (0, 68),
        (-68, 0),
        (50, -50),
        (50, 50),
        (-50, 50),
        (-50, -50),
    )
    for marker in markers:
        origin_x, origin_y = viewport.point(marker.latitude, marker.longitude)
        radius = 15 if len(marker.label) <= 2 else 12
        x, y = origin_x, origin_y
        for offset_x, offset_y in marker_offsets:
            candidate_x = origin_x + offset_x
            candidate_y = origin_y + offset_y
            inside_plot = (
                margin + radius <= candidate_x <= width - margin - radius
                and header + radius <= candidate_y <= height - margin - radius
            )
            clear = all(
                radius + other_radius + 6
                <= math.hypot(candidate_x - other_x, candidate_y - other_y)
                for other_x, other_y, other_radius in occupied_markers
            )
            if inside_plot and clear:
                x, y = candidate_x, candidate_y
                break
        if (x, y) != (origin_x, origin_y):
            draw.line((origin_x, origin_y, x, y), fill=marker.color, width=2)
            draw.ellipse(
                (origin_x - 4, origin_y - 4, origin_x + 4, origin_y + 4),
                fill=marker.color,
                outline="#ffffff",
                width=1,
            )
        draw.ellipse(
            (x - radius - 3, y - radius - 3, x + radius + 3, y + radius + 3),
            fill="#ffffff",
        )
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=marker.color,
            outline="#ffffff",
            width=2,
        )
        if marker.label:
            draw.text(
                (x, y - 1),
                marker.label[:3],
                font=marker_font,
                fill="#ffffff",
                anchor="mm",
            )
        occupied_markers.append((x, y, radius))
        draw.polygon(
            (
                (width - 50, header + 10),
                (width - 58, header + 30),
                (width - 42, header + 30),
            ),
            fill="#344054",
        )
    draw.text(
        (width - 50, header + 42),
        "N",
        font=label_font,
        fill="#344054",
        anchor="mm",
    )
    draw.text(
        (width - margin, height - 18),
        "© OpenStreetMap contributors",
        font=_font(13),
        fill="#777269",
        anchor="ra",
    )

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
