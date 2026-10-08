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
    raise SystemExit(update(arguments))
