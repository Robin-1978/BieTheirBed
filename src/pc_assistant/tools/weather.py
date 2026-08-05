from __future__ import annotations

import httpx
from typing import Any

from pc_assistant.tools.base import ToolBase, tool


@tool(name="weather", description="Get weather for a place.", skim_description="Weather and forecast.")
class WeatherTool(ToolBase):
    name = "weather"
    description = "Get current weather and forecast for any city or location"

    async def execute(self, **kwargs: Any) -> Any:
        location = kwargs.get("location", "")
        forecast = kwargs.get("forecast", "current")
        if not location:
            return {"error": "No location provided"}
        try:
            url = f"https://wttr.in/{location}?format=j1"
            headers = {"User-Agent": "curl/7.68.0"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            return {"error": f"Failed to fetch weather: {e}"}
        except Exception as e:
            return {"error": f"Weather lookup failed: {e}"}

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

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or location (e.g. 'Shanghai', 'Beijing', 'New York')",
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

    def core_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Weather for a location. Optional forecast.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "forecast": {"type": "string", "enum": ["current", "forecast"], "default": "current"},
                },
                "required": ["location"],
            },
        }
