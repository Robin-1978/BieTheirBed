"""Keyless reverse-geocoding enrichment for coordinate-only device fixes.

Uses the public Photon instance (Komoot, OSM data, no key, no signup).
Only invoked when the App snapshot has coordinates but no readable
address (e.g. GMS-less ROMs whose system geocoder returned nothing).
Results are cached in-process by rounded coordinate cell.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

_COORD_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse"
_TIMEOUT_SECONDS = 4.0
_CACHE_TTL_SECONDS = 24 * 3600

_cache: dict[tuple[float, float], tuple[float, str]] = {}


def _fetch_photon(lat: float, lon: float) -> dict:
    with httpx.Client(timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "KnoaPersonal/1.0"}) as client:
        response = client.get(
            _PHOTON_REVERSE_URL,
            params={"lat": lat, "lon": lon, "lang": "zh"},
        )
        response.raise_for_status()
        payload = response.json()
    features = payload.get("features") or []
    return features[0].get("properties", {}) if features else {}


def _format_properties(props: dict) -> str:
    parts = [
        str(props.get("name") or ""),
        str(props.get("district") or props.get("locality") or ""),
        str(props.get("city") or props.get("county") or ""),
        str(props.get("state") or ""),
    ]
    seen: set[str] = set()
    ordered = [part for part in parts if part and part not in seen and not seen.add(part)]  # type: ignore[func-returns-value]
    return "".join(ordered)


def enrich_device_location(text: str) -> str:
    """Append a keyless reverse-geocoded neighbourhood when useful.

    Returns the input unchanged when it already carries a readable
    address, has no coordinates, or the lookup fails/timeouts.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return text
    match = _COORD_RE.search(cleaned)
    if match is None:
        return text
    without_coords = _COORD_RE.sub("", cleaned)
    if _CJK_RE.search(without_coords.replace("未知位置", "")):
        return text
    try:
        lat, lon = float(match.group(1)), float(match.group(2))
    except ValueError:
        return text
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return text
    cell = (round(lat, 3), round(lon, 3))
    now = time.monotonic()
    cached = _cache.get(cell)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        area = cached[1]
    else:
        try:
            area = _format_properties(_fetch_photon(lat, lon))
        except Exception as exc:  # noqa: BLE001 - enrichment must never fail a turn
            logger.debug("Photon reverse-geocode failed for %s,%s: %s", lat, lon, exc)
            return text
        _cache[cell] = (now, area)
    if not area:
        return text
    return f"{cleaned} (附近：{area})"
