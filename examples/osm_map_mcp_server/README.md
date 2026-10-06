# Offline China OSM Map MCP

This package provides offline place search, reverse geocoding, nearby search,
place details, walking/cycling/driving routes, distance calculation and
WGS84/GCJ-02/BD-09 conversion through a standard MCP stdio server.

The source PBF remains the authoritative snapshot. The MCP process only reads
the generated SQLite database; it never scans the PBF while serving queries.

## Build the first database

The production builder is C++17 with libosmium and SQLite. The helper builds it
into `bin/osm_map_builder`; when system headers are unavailable it downloads
the Debian development packages into the build directory without using sudo.

```bash
./examples/osm_map_mcp_server/build_cpp_builder.sh /disk/dev/osm-map-cpp-build
mkdir -p /tmp/osm-map-database-build /disk/dev/osm-map-index

examples/osm_map_mcp_server/bin/osm_map_builder \
 /disk/osm/extracts/china-latest.osm.pbf \
 /tmp/osm-map-database-build/china.sqlite \
 --temp-dir /disk/dev/osm-map-index
```

Build SQLite on fast local storage. `/disk` is suitable for the sparse location
index and SQLite sort spill files, but its random write performance makes it a
poor database build target. The builder writes route tables sequentially and
creates all B-tree, FTS5 and RTree indexes after the base data is complete. It
marks the database `ready` only after SQLite integrity, layer count, FTS and
RTree checks pass. An interrupted build remains unavailable and can be safely
deleted and rebuilt from the retained PBF.

The 2026-09-27 China snapshot built in 47 minutes 52 seconds. The 1.60 GB PBF
produced a 21.59 GB SQLite database with 4,745,908 searchable features,
98,241,267 route nodes and 102,784,644 route edges.

After validation, copy the file sequentially to the large disk and expose it at
the default runtime path:

```bash
mkdir -p /disk/dev/osm-map-live ~/.local/share/knoa/osm-map
cp --reflink=auto /tmp/osm-map-database-build/china.sqlite \
 /disk/dev/osm-map-live/.china.sqlite.next
mv /disk/dev/osm-map-live/.china.sqlite.next \
 /disk/dev/osm-map-live/china.sqlite
ln -sfn /disk/dev/osm-map-live/china.sqlite \
 ~/.local/share/knoa/osm-map/china.sqlite
```

## Run and deploy

The default database path is
`~/.local/share/knoa/osm-map/china.sqlite`. Override it with `OSM_MAP_DB`.

```bash
OSM_MAP_DB="$HOME/.local/share/knoa/osm-map/china.sqlite" \
 .venv/bin/python examples/osm_map_mcp_server/server.py

knoa mcp-package-deploy ./examples/osm_map_mcp_server osm-map
```

All eight tools are read-only and local:

- `map.search_places`
- `map.reverse_geocode`
- `map.nearby_search`
- `map.get_place`
- `map.route`
- `map.distance`
- `map.convert_coordinates`
- `map.dataset_info`

The first six spatial tools return structured text and attach a 1200×750 PNG
map by default. Maps use a high-contrast road hierarchy, distribute road data
across the complete viewport, prioritize major road names, and keep roads used
by a route labeled above the route line. They also show numbered result
markers, search radii, place boundaries or route lines as appropriate. Set
`include_map_image=false` when only structured data is needed. Coordinate
conversion and dataset metadata remain text only.

`map://dataset` exposes the source replication timestamp, sequence, bounds,
counts and capability status. `map://categories` exposes common bilingual OSM
category names.

## Keep the data current

The PBF header points to Geofabrik's China replication service. The updater
downloads only `.osc.gz` changes, generates a new PBF beside the live one,
builds and validates a new SQLite database, then atomically rotates both files.
One previous PBF and database are retained with a `.previous` suffix.

```bash
/usr/bin/python3 examples/osm_map_mcp_server/update_map.py \
 --source /disk/osm/extracts/china-latest.osm.pbf \
 --database ~/.local/share/knoa/osm-map/china.sqlite \
 --work-dir /disk/dev/osm-map-update \
 --database-build-dir /tmp/osm-map-database-build \
 --index-temp-dir /disk/dev/osm-map-index
```

Run this command daily after the initial build. If the PBF is already current,
the updater exits without rebuilding SQLite. During a rebuild the MCP keeps
serving the previous complete database. A failed download, merge, build or
validation leaves the active database unchanged. The host must be able to
reach the PBF header's Geofabrik replication URL; a network failure is logged
and the next scheduled run can safely retry.

Example cron entry for a daily 03:20 update:

```cron
20 3 * * * /usr/bin/python3 /absolute/path/examples/osm_map_mcp_server/update_map.py --source /disk/osm/extracts/china-latest.osm.pbf --database /home/USER/.local/share/knoa/osm-map/china.sqlite --work-dir /disk/dev/osm-map-update --database-build-dir /tmp/osm-map-database-build --index-temp-dir /disk/dev/osm-map-index >> /home/USER/.local/share/knoa/osm-map/update.log 2>&1
```

OSM does not contain authoritative public transit timetables, live traffic or
weather. `map.dataset_info` reports those capabilities as `unsupported` so an
Agent can select another provider.
