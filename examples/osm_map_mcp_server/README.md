# Offline China OSM Map MCP

This package provides offline place search, reverse geocoding, nearby search,
place details, walking/cycling/driving routes, distance calculation and
WGS84/GCJ-02/BD-09 conversion through a standard MCP stdio server.

The retained PBF is a disaster-recovery checkpoint. The complete OSM object
SQLite database is the current normalized state, and the MCP process reads only
the derived service SQLite database. Serving and daily updates never scan the
country PBF.

## Build the first database

The production builder is C++17 with libosmium and SQLite. The helper builds it
into `bin/osm_map_builder`; when system headers are unavailable it downloads
the Debian development packages into the build directory without using sudo.

```bash
./examples/osm_map_mcp_server/build_cpp_builder.sh /disk/dev/osm-map-cpp-build
mkdir -p /disk/dev/osm-map-build /disk/dev/osm-map-index

examples/osm_map_mcp_server/bin/osm_map_builder \
 /disk/osm/extracts/china-latest.osm.pbf \
 /disk/dev/osm-map-build/china.sqlite.building \
 --temp-dir /disk/dev/osm-map-index
```

The build database, sparse location index and SQLite sort spill files stay on
`/disk`; the developer home and NVMe are not used for large persistent or
temporary map data. The builder writes route tables sequentially and creates
all B-tree, FTS5 and RTree indexes after the base data is complete. It uses
bounded samples for query-planner statistics, then marks the database `ready`
only after SQLite integrity, layer count, FTS and RTree checks pass. An
interrupted `.building` file remains unavailable and can be safely deleted and
rebuilt from the PBF checkpoint.

The 2026-09-27 China snapshot built in 47 minutes 52 seconds. The 1.60 GB PBF
produced a 21.59 GB SQLite database with 4,745,908 searchable features,
98,241,267 route nodes and 102,784,644 route edges.

After validation, atomically publish the file on the same large disk and expose
the default runtime path:

```bash
mkdir -p /disk/dev/osm-map-live ~/.local/share/knoa/osm-map
mv /disk/dev/osm-map-build/china.sqlite.building \
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

The first six spatial tools return structured text and attach a 1600×1000 PNG
map by default. Maps are drawn at twice the output resolution and downsampled
for clean road and text edges. Water, green space, residential, industrial and
building polygons form the base map; roads use a clear hierarchy, major road
names are prioritized, and roads used by a route stay labeled above the route
line. Numbered result markers, search radii, place boundaries and route lines
are added as appropriate. Set
`include_map_image=false` when only structured data is needed. Coordinate
conversion and dataset metadata remain text only.

The renderer fits the visible bounds in Web Mercator, prefetches beyond every
image edge and queries intersecting full OSM geometries. Roads, waterways,
railways, coastlines and polygons therefore continue through the viewport
instead of ending where a sampled routing edge or database grid cell ends.

`map://dataset` exposes the current database replication timestamp and sequence,
the retained PBF checkpoint sequence, bounds, counts and capability status.
`map://categories` exposes common bilingual OSM category names.

## Keep the data current

The updater reads the replication URL from metadata and downloads only the
standard `.osc.gz` changes after the object database sequence. It applies
complete node, way and relation versions by OSM ID, uses the reverse member
indexes to find affected parents before and after the change, and stores that
scope in a durable pending journal.

The C++ delta builder reads the selected objects directly from
`china-osm-objects.sqlite` and writes a small derived delta SQLite database. It
does not produce an intermediate PBF. The service delta is merged into
`china.sqlite` in one WAL transaction, including FTS, RTree, areas, route edges
and route-node modes. A crash between the two database commits is recovered
from the pending journal on the next run.

The retained PBF is a checkpoint and is not rewritten by daily updates. Applied
compressed diffs are moved to `WORK_DIR/applied`, so the checkpoint plus the
archive can reconstruct the current object state. A maintenance window can
occasionally compact those diffs into a newer PBF checkpoint. Keep archived
diffs until that checkpoint has been validated.

Older service databases need a one-time `route_edges_way` index migration.
After it exists, every update scales with the changed objects and their actual
parent/reference closure.

```bash
/usr/bin/python3 examples/osm_map_mcp_server/update_map.py \
 --source /disk/dev/osm-map-live/china-latest.osm.pbf \
 --database /disk/dev/osm-map-live/china.sqlite \
 --dependency-database /disk/dev/osm-map-live/china-osm-objects.sqlite \
 --work-dir /disk/dev/osm-map-incremental \
 --index-temp-dir /disk/dev/osm-map-incremental/sqlite-tmp
```

Run the updater frequently to keep batches small. Network failures and failed
service merges are safe to retry. All databases, archived diffs and SQLite
spill files remain under `/disk`.

If the object database is lost, recreate it from the retained compatible PBF
with `osm_dependency_builder`, then replay the archived diffs. A full service
database rebuild remains the last-resort recovery path:

```bash
/usr/bin/python3 examples/osm_map_mcp_server/rebuild_map.py \
 --source /disk/dev/osm-map-live/china-latest.osm.pbf \
 --database /disk/dev/osm-map-live/china.sqlite \
 --work-dir /disk/dev/osm-map-rebuild \
 --database-build-dir /disk/dev/osm-map-build \
 --index-temp-dir /disk/dev/osm-map-index
```

Example cron entry for a 15-minute update cadence:

```cron
*/15 * * * * cd /absolute/path && /usr/bin/flock -n /disk/dev/osm-map-incremental/cron-update.lock /usr/bin/nice -n 15 /usr/bin/ionice -c2 -n7 /usr/bin/python3 examples/osm_map_mcp_server/update_map.py --source /disk/dev/osm-map-live/china-latest.osm.pbf --database /disk/dev/osm-map-live/china.sqlite --dependency-database /disk/dev/osm-map-live/china-osm-objects.sqlite --work-dir /disk/dev/osm-map-incremental --index-temp-dir /disk/dev/osm-map-incremental/sqlite-tmp >> /disk/dev/osm-map-incremental/daily-update.log 2>&1
```

OSM does not contain authoritative public transit timetables, live traffic or
weather. `map.dataset_info` reports those capabilities as `unsupported` so an
Agent can select another provider.
