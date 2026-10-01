"""Standard MCP stdio adapter for the offline OSM map service."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp import types
from mcp.server.lowlevel.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

try:
    from .map_service import MapError, MapService, MapSettings, convert_coordinate
except ImportError:  # pragma: no cover - direct script execution
    from map_service import MapError, MapService, MapSettings, convert_coordinate


logger = logging.getLogger("osm-map-mcp")
_DATASET_URI = "map://dataset"
_CATEGORIES_URI = "map://categories"


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_COORDINATE_SYSTEM = {
    "type": "string",
    "enum": ["wgs84", "gcj02", "bd09"],
    "default": "wgs84",
}
_LATITUDE = {"type": "number", "minimum": -90, "maximum": 90}
_LONGITUDE = {"type": "number", "minimum": -180, "maximum": 180}
_READ_ONLY = types.ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="map.search_places",
            title="Search offline map places",
            description=(
                "Search Chinese places, addresses, roads, brands and POIs in the local OSM "
                "dataset. Use region to disambiguate names and coordinates to bias ranking."
            ),
            input_schema=_schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "region": {"type": "string", "maxLength": 100},
                    "latitude": _LATITUDE,
                    "longitude": _LONGITUDE,
                    "coordinate_system": _COORDINATE_SYSTEM,
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 10,
                    },
                },
                ["query"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.reverse_geocode",
            title="Reverse geocode offline coordinates",
            description=(
                "Return containing administrative areas plus nearby addresses, roads and "
                "places for one coordinate. Latitude and longitude are separate fields."
            ),
            input_schema=_schema(
                {
                    "latitude": _LATITUDE,
                    "longitude": _LONGITUDE,
                    "coordinate_system": _COORDINATE_SYSTEM,
                    "nearby_limit": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 10,
                        "default": 5,
                    },
                },
                ["latitude", "longitude"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.nearby_search",
            title="Search nearby offline places",
            description=(
                "Find places within a true circular radius. Optional categories accept an "
                "OSM value such as cafe or a qualified value such as amenity:cafe."
            ),
            input_schema=_schema(
                {
                    "latitude": _LATITUDE,
                    "longitude": _LONGITUDE,
                    "coordinate_system": _COORDINATE_SYSTEM,
                    "radius_m": {
                        "type": "integer",
                        "minimum": 10,
                        "maximum": 50000,
                        "default": 1000,
                    },
                    "query": {"type": "string", "maxLength": 200},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                        "maxItems": 20,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                },
                ["latitude", "longitude"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.get_place",
            title="Read offline place details",
            description="Read details for one stable place_id returned by another map tool.",
            input_schema=_schema(
                {
                    "place_id": {
                        "type": "string",
                        "pattern": r"^osm:(node|way|relation|area):-?[0-9]+:[a-z_]+$",
                    },
                    "coordinate_system": _COORDINATE_SYSTEM,
                },
                ["place_id"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.route",
            title="Plan an offline route",
            description=(
                "Plan a walking, cycling or driving route on the local OSM road graph. "
                "origin and destination accept place text or 'latitude,longitude'."
            ),
            input_schema=_schema(
                {
                    "origin": {"type": "string", "minLength": 1, "maxLength": 300},
                    "destination": {"type": "string", "minLength": 1, "maxLength": 300},
                    "mode": {
                        "type": "string",
                        "enum": ["walking", "cycling", "driving"],
                        "default": "driving",
                    },
                    "coordinate_system": _COORDINATE_SYSTEM,
                    "output_coordinate_system": _COORDINATE_SYSTEM,
                },
                ["origin", "destination"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.distance",
            title="Calculate offline map distances",
            description=(
                "Calculate straight-line distance for up to 16 origins, or walking, cycling "
                "or driving distance for up to 4 origins, to one destination."
            ),
            input_schema=_schema(
                {
                    "origins": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                    "destination": {"type": "string", "minLength": 1, "maxLength": 300},
                    "mode": {
                        "type": "string",
                        "enum": ["straight", "walking", "cycling", "driving"],
                        "default": "straight",
                    },
                    "coordinate_system": _COORDINATE_SYSTEM,
                },
                ["origins", "destination"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.convert_coordinates",
            title="Convert Chinese map coordinates",
            description=(
                "Convert up to 100 coordinates among WGS84, GCJ-02 and BD-09. Each input "
                "coordinate uses explicit latitude and longitude fields."
            ),
            input_schema=_schema(
                {
                    "coordinates": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": _schema(
                            {"latitude": _LATITUDE, "longitude": _LONGITUDE},
                            ["latitude", "longitude"],
                        ),
                    },
                    "source": _COORDINATE_SYSTEM,
                    "target": _COORDINATE_SYSTEM,
                },
                ["coordinates", "source", "target"],
            ),
            annotations=_READ_ONLY,
        ),
        types.Tool(
            name="map.dataset_info",
            title="Inspect offline map coverage",
            description=(
                "Return source date, geographic bounds, row counts, coordinate systems and "
                "the exact capabilities available in this local map snapshot."
            ),
            input_schema=_schema({}, []),
            annotations=_READ_ONLY,
        ),
    ]


class OSMMapMCPApplication:
    def __init__(self, settings: MapSettings | None = None) -> None:
        self.service = MapService(settings or MapSettings.from_env())
        self.server = Server(
            "osm-map",
            version="1.0.0",
            instructions=(
                "Offline China map backed by OpenStreetMap. Coordinates use explicit "
                "latitude/longitude fields; route text coordinates use latitude,longitude. "
                "Inspect map://dataset when source age or supported capabilities matter."
            ),
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )

    async def _list_resources(
        self, _context: Any, _params: Any
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=_DATASET_URI,
                    name="Offline OSM map dataset",
                    description="Map snapshot date, coverage, counts and capability status.",
                    mime_type="application/json",
                    annotations=types.Annotations(audience=["assistant"], priority=1.0),
                ),
                types.Resource(
                    uri=_CATEGORIES_URI,
                    name="Offline OSM map categories",
                    description="Common bilingual category vocabulary accepted by nearby search.",
                    mime_type="application/json",
                    annotations=types.Annotations(audience=["assistant"]),
                ),
            ]
        )

    async def _read_resource(
        self,
        _context: Any,
        params: types.ReadResourceRequestParams,
    ) -> types.ReadResourceResult:
        if params.uri == _DATASET_URI:
            result = await asyncio.to_thread(self.service.dataset_info)
        elif params.uri == _CATEGORIES_URI:
            result = self.service.categories()
        else:
            raise LookupError("unknown map Resource URI")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=params.uri,
                    mime_type="application/json",
                    text=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                )
            ]
        )

    async def _list_tools(self, _context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_tool_definitions())

    async def _call_tool(
        self,
        _context: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        name = params.name
        arguments = dict(params.arguments or {})
        started = asyncio.get_running_loop().time()
        try:
            if name == "map.search_places":
                result = await asyncio.to_thread(
                    self.service.search_places,
                    str(arguments["query"]),
                    region=str(arguments.get("region") or ""),
                    latitude=arguments.get("latitude"),
                    longitude=arguments.get("longitude"),
                    coordinate_system=str(
                        arguments.get("coordinate_system") or "wgs84"
                    ),
                    limit=int(arguments.get("limit", 10)),
                )
            elif name == "map.reverse_geocode":
                result = await asyncio.to_thread(
                    self.service.reverse_geocode,
                    float(arguments["latitude"]),
                    float(arguments["longitude"]),
                    coordinate_system=str(
                        arguments.get("coordinate_system") or "wgs84"
                    ),
                    nearby_limit=int(arguments.get("nearby_limit", 5)),
                )
            elif name == "map.nearby_search":
                result = await asyncio.to_thread(
                    self.service.nearby_search,
                    float(arguments["latitude"]),
                    float(arguments["longitude"]),
                    coordinate_system=str(
                        arguments.get("coordinate_system") or "wgs84"
                    ),
                    radius_m=int(arguments.get("radius_m", 1000)),
                    query=str(arguments.get("query") or ""),
                    categories=tuple(arguments.get("categories") or ()),
                    limit=int(arguments.get("limit", 20)),
                )
            elif name == "map.get_place":
                result = await asyncio.to_thread(
                    self.service.get_place,
                    str(arguments["place_id"]),
                    coordinate_system=str(
                        arguments.get("coordinate_system") or "wgs84"
                    ),
                )
            elif name == "map.route":
                result = await asyncio.to_thread(
                    self.service.route,
                    str(arguments["origin"]),
                    str(arguments["destination"]),
                    mode=str(arguments.get("mode") or "driving"),
                    coordinate_system=str(
                        arguments.get("coordinate_system") or "wgs84"
                    ),
                    output_coordinate_system=str(
                        arguments.get("output_coordinate_system") or "wgs84"
                    ),
                )
            elif name == "map.distance":
                result = await asyncio.to_thread(
                    self.service.distance,
                    tuple(str(value) for value in arguments["origins"]),
                    str(arguments["destination"]),
                    mode=str(arguments.get("mode") or "straight"),
                    coordinate_system=str(
                        arguments.get("coordinate_system") or "wgs84"
                    ),
                )
            elif name == "map.convert_coordinates":
                source, target = str(arguments["source"]), str(arguments["target"])
                converted = []
                for coordinate in arguments["coordinates"]:
                    latitude, longitude = convert_coordinate(
                        float(coordinate["latitude"]),
                        float(coordinate["longitude"]),
                        source,
                        target,
                    )
                    converted.append(
                        {
                            "latitude": round(latitude, 7),
                            "longitude": round(longitude, 7),
                        }
                    )
                result = {"source": source, "target": target, "coordinates": converted}
            elif name == "map.dataset_info":
                result = await asyncio.to_thread(self.service.dataset_info)
            else:
                raise MapError("unknown_tool", f"unknown map tool: {name}")
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            return types.CallToolResult(
                content=[types.TextContent(text=encoded)],
                structured_content=result,
            )
        except asyncio.CancelledError:
            raise
        except MapError as exc:
            result = exc.as_dict()
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        text=json.dumps(
                            result, ensure_ascii=False, separators=(",", ":")
                        )
                    )
                ],
                structured_content=result,
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Map tool failed: %s (%s)", name, type(exc).__name__)
            result = {"code": "internal_error", "message": "offline map query failed"}
            return types.CallToolResult(
                content=[
                    types.TextContent(text=json.dumps(result, separators=(",", ":")))
                ],
                structured_content=result,
                is_error=True,
            )
        finally:
            elapsed = asyncio.get_running_loop().time() - started
            logger.info("map tool=%s elapsed_ms=%d", name, round(elapsed * 1000))

    def initialization_options(self):
        return self.server.create_initialization_options(NotificationOptions())

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.initialization_options(),
            )


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await OSMMapMCPApplication().run_stdio()


if __name__ == "__main__":
    asyncio.run(_main())
