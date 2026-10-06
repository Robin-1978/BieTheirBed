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


def _road_style(highway: str) -> tuple[str, str, int]:
    if highway == "motorway":
        return "#f59e0b", "#fff1c7", 8
    if highway == "trunk":
        return "#f7b955", "#fff4d8", 7
    if highway == "primary":
        return "#ffd166", "#ffffff", 7
    if highway == "secondary":
        return "#ffffff", "#cbd5e1", 6
    if highway == "tertiary":
        return "#ffffff", "#d7dee8", 5
    if highway in {"footway", "path", "pedestrian", "steps"}:
        return "#dbe4ee", "#f8fafc", 2
    return "#ffffff", "#e2e8f0", 3


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


def _label_box(draw, xy, text: str, font, *, stroke_width: int = 0):
    left, top, right, bottom = draw.textbbox(
        xy,
        text,
        font=font,
        anchor="mm",
        stroke_width=stroke_width,
    )
    return (left - 5, top - 3, right + 5, bottom + 3)


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
) -> list[tuple[int, int, int, int]]:
    occupied: list[tuple[int, int, int, int]] = []
    offsets = ((0, -23), (23, 0), (-23, 0), (0, 23), (32, -22), (-32, -22))
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
            box = _label_box(draw, xy, text, font, stroke_width=1)
            inside = (
                margin <= box[0]
                and box[2] <= width - margin
                and header + 4 <= box[1]
                and box[3] <= height - margin
            )
            if inside and not any(_boxes_overlap(box, item) for item in occupied):
                selected = (xy, box)
                break
        if selected is None:
            continue
        xy, box = selected
        draw.rounded_rectangle(
            box,
            radius=6,
            fill="#ffffff",
            outline="#60a5fa",
            width=1,
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
        if total_length < 18:
            continue
        ranked_anchors = sorted(
            rendered,
            key=lambda item: item[0] * 2 - item[1] * 0.12,
            reverse=True,
        )
        anchor = ranked_anchors[0]
        score = (
            importance.get(best_rank, 0)
            + min(total_length, 500) * 0.12
            - anchor[1] * 0.08
        )
        candidates.append(
            (score, name[:16], tuple(item[2] for item in ranked_anchors[:12]))
        )

    for _score, text, anchors in sorted(
        candidates, key=lambda item: item[0], reverse=True
    )[:32]:
        selected = None
        for xy in anchors:
            box = _label_box(draw, xy, text, font, stroke_width=3)
            inside = (
                margin <= box[0]
                and box[2] <= width - margin
                and header + 4 <= box[1]
                and box[3] <= height - margin
            )
            if inside and not any(
                _boxes_overlap(box, item, gap=8) for item in occupied
            ):
                selected = (xy, box)
                break
        if selected is None:
            continue
        xy, box = selected
        _draw_centered_text(draw, xy, text, font, "#334155")
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

    width, height, header, margin = 1200, 750, 96, 44
    viewport = _Viewport(
        bounds,
        width=width,
        height=height,
        top=header,
        margin=margin,
    )
    image = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(image)
    title_font = _font(30, bold=True)
    subtitle_font = _font(19)
    label_font = _font(17)
    route_label_font = _font(17, bold=True)
    marker_font = _font(18, bold=True)

    for road in sorted(roads, key=lambda item: _road_rank(item.highway), reverse=True):
        start = viewport.point(*road.start)
        end = viewport.point(*road.end)
        fill, casing, road_width = _road_style(road.highway)
        draw.line((start, end), fill=casing, width=road_width + 3)
        draw.line((start, end), fill=fill, width=road_width)

    draw.rectangle((0, 0, width, header), fill="#ffffff")
    draw.line((0, header - 1, width, header - 1), fill="#dbe3ed", width=1)
    draw.text((margin, 16), title[:64], font=title_font, fill="#0f172a")
    draw.text((margin, 57), subtitle[:96], font=subtitle_font, fill="#475569")

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
            width=3,
        )

    for line in lines:
        if len(line.points) < 2:
            continue
        pixels = [viewport.point(*point) for point in line.points]
        if line.outline:
            draw.line(pixels, fill="#ffffff", width=line.width + 6, joint="curve")
        draw.line(pixels, fill=line.color, width=line.width, joint="curve")

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
    for marker in markers:
        origin_x, origin_y = viewport.point(marker.latitude, marker.longitude)
        radius = 17 if len(marker.label) <= 2 else 14
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
            marker_box = (
                candidate_x - radius - 4,
                candidate_y - radius - 4,
                candidate_x + radius + 4,
                candidate_y + radius + 4,
            )
            clear_labels = not any(
                _boxes_overlap(marker_box, label_box, gap=5)
                for label_box in occupied_labels
            )
            if inside_plot and clear and clear_labels:
                x, y = candidate_x, candidate_y
                break
        if (x, y) != (origin_x, origin_y):
            draw.line((origin_x, origin_y, x, y), fill=marker.color, width=2)
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
            (width - 58, header + 12),
            (width - 67, header + 36),
            (width - 49, header + 36),
        ),
        fill="#344054",
    )
    draw.text(
        (width - 58, header + 51),
        "N",
        font=label_font,
        fill="#344054",
        anchor="mm",
    )
    draw.text(
        (width - margin, height - 20),
        "© OpenStreetMap contributors",
        font=_font(14),
        fill="#64748b",
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
