#!/usr/bin/env python3
"""Run the object-level incremental OSM map updater."""

try:
    from .incremental_update import _parser, update
except ImportError:
    from incremental_update import _parser, update


if __name__ == "__main__":
    arguments = _parser().parse_args()
    arguments.diff_batch_mb = max(1, min(arguments.diff_batch_mb, 1024))
    arguments.socket_timeout = max(10, min(arguments.socket_timeout, 600))
    arguments.build_batch_size = max(1_000, min(arguments.build_batch_size, 500_000))
    arguments.max_ancestor_members = max(0, min(arguments.max_ancestor_members, 100_000_000))
    arguments.max_selection_nodes = max(0, min(arguments.max_selection_nodes, 1_000_000_000))
    arguments.deferred_window_start = max(0, min(arguments.deferred_window_start, 23))
    arguments.deferred_window_end = max(0, min(arguments.deferred_window_end, 23))
    arguments.deferred_max_age_hours = max(1.0, min(arguments.deferred_max_age_hours, 720.0))
    raise SystemExit(update(arguments))
