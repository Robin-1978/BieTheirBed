from __future__ import annotations

import httpx
import re
from typing import Any
from urllib.parse import quote

from knoa_platform.tools.base import ToolBase, ToolCapability, ToolEffect, ToolRisk
from knoa_platform.tools.http_limits import read_limited_json


_MAX_WEATHER_RESPONSE_BYTES = 1024 * 1024

_COORD_RE = re.compile(r"\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


def weather_fallback_queries(query: str) -> list[str]:
    """Broaden an over-specific Chinese address when wttr.in 500s.

    Seen in production: "上海市嘉定区墨玉北路" 500s while
    "上海市嘉定区" succeeds. Try full query first, then district, then city.
    """
    candidates = [query]
    city = re.search(r"^(.+?市)", query)
    district = re.search(r"^(.+?区)", query)
    if district is not None and district.group(1) != query:
        candidates.append(district.group(1))
    if city is not None and city.group(1) not in candidates:
        candidates.append(city.group(1))
    return candidates


def normalize_weather_location(raw: str) -> str:
    """Prefer coordinates when device_location returned them.

    device_location emits strings like "(31.3,121.2 ±30m)" or
    "上海市嘉定区... (31.2,121.1 ±25m)". wttr.in resolves "lat,lon"
    precisely but 500s on over-specific street addresses (seen with
    "上海市嘉定区墨玉北路"). Extract the coordinate pair when present;
    otherwise fall back to the raw text.
    """
    text = (raw or "").strip()
    if not text:
        return text
    match = _COORD_RE.search(text)
    if match is None:
        return text
    try:
        lat, lon = float(match.group(1)), float(match.group(2))
    except ValueError:
        return text
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return text
    return f"{lat},{lon}"


class WeatherTool(ToolBase):
    name = "weather"
    description = (
        "Get weather for a location. Accepts a city/place name or "
        "'lat,lon' coordinates. When device_location just returned coordinates, "
        "pass that location string through verbatim instead of guessing a city."
    )
    effect = ToolEffect.READ_ONLY
    capabilities = frozenset({ToolCapability.NETWORK})
    risk = ToolRisk.LOW

    async def execute(self, **kwargs: Any) -> Any:
        location = kwargs.get("location", "")
        forecast = kwargs.get("forecast", "current")
        if not location:
            return {"error": "No location provided"}
        if not isinstance(location, str) or len(location) > 200:
            return {"error": "Location must contain at most 200 characters"}
        query = normalize_weather_location(location)
        last_error = ""
        for attempt in weather_fallback_queries(query):
            try:
                url = f"https://wttr.in/{quote(attempt, safe='')}?format=j1"
                headers = {"User-Agent": "curl/7.68.0"}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    async with client.stream("GET", url, headers=headers) as resp:
                        resp.raise_for_status()
                        data = await read_limited_json(
                            resp,
                            _MAX_WEATHER_RESPONSE_BYTES,
                        )
                break
            except httpx.HTTPError as e:
                last_error = f"Failed to fetch weather: {e}"
                continue
            except Exception as e:
                return {"error": f"Weather lookup failed: {e}"}
        else:
            return {"error": last_error or "Failed to fetch weather"}

        try:
            current = data.get("current_condition", [{}])[0]
            area = data.get("nearest_area", [{}])[0]
            result = {
                "location": area.get("areaName", [{}])[0].get("value", location),
                "region": area.get("region", [{}])[0].get("value", ""),
                "country": area.get("country", [{}])[0].get("value", ""),
                "temperature": f"{current.get('temp_C', '?')}°C ({current.get('temp_F', '?')}°F)",
                "feels_like": f"{current.get('FeelsLikeC', '?')}°C",
                "humidity": f"{current.get('humidity', '?')}%",
                "weather": current.get("weatherDesc", [{}])[0].get("value", "unknown"),
                "wind": f"{current.get('windspeedKmph', '?')} km/h {current.get('winddir16Point', '')}",
                "visibility": f"{current.get('visibility', '?')} km",
                "pressure": f"{current.get('pressure', '?')} hPa",
                "uv_index": current.get("uvIndex", "?"),
            }
            if forecast == "forecast":
                days = []
                for day in data.get("weather", []):
                    days.append({
                        "date": day.get("date", ""),
                        "max_temp": f"{day.get('maxtempC', '?')}°C",
                        "min_temp": f"{day.get('mintempC', '?')}°C",
                        "avg_temp": f"{day.get('avgtempC', '?')}°C",
                        "weather": day.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "unknown") if len(day.get("hourly", [])) > 4 else "unknown",
                    })
                result["forecast"] = days
            return result
        except (KeyError, IndexError) as e:
            return {"error": f"Failed to parse weather data: {e}", "raw": str(data)[:500]}

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "City name, readable address, or 'lat,lon' coordinates from device_location (pass through verbatim, e.g. '(31.3,121.2)'). Prefer coordinates when available; avoid over-specific street addresses that weather lookup cannot resolve.",
                    },
                    "forecast": {
                        "type": "string",
                        "enum": ["current", "forecast"],
                        "description": "Get current weather only, or include 3-day forecast (default: current)",
                    },
                },
                "required": ["location"],
            },
        }

    def skim_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "maxLength": 200},
                    "forecast": {"type": "string", "enum": ["current", "forecast"], "description": "forecast adds 3 days"},
                },
                "required": ["location"],
            },
        }
