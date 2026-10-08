#!/usr/bin/env python3
"""Atomically update an OSM PBF and rebuild the offline map database."""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import time


def _run(command: list[str]) -> int:
    print("run:", " ".join(command), flush=True)
    return subprocess.run(command, check=False).returncode


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _stage_file(source: Path, target: Path) -> None:
    """Copy a completed file beside its live target and make it durable."""
    _remove(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_stream, target.open("xb") as target_stream:
        shutil.copyfileobj(source_stream, target_stream, length=16 * 1024 * 1024)
        target_stream.flush()
        os.fsync(target_stream.fileno())
    shutil.copystat(source, target)
    _fsync_directory(target.parent)


def _rotate(current: Path, replacement: Path, previous: Path) -> None:
    _remove(previous)
    if current.exists():
        os.replace(current, previous)
    try:
        os.replace(replacement, current)
        _fsync_directory(current.parent)
    except Exception:
        if previous.exists() and not current.exists():
            os.replace(previous, current)
        raise


def _restore_previous(current: Path, previous: Path) -> None:
    if not previous.exists():
        return
    _remove(current)
    os.replace(previous, current)
    _fsync_directory(current.parent)


def update(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    database = args.database.resolve()
    work = args.work_dir.resolve()
    database_build_dir = args.database_build_dir.resolve()
    if not source.is_file():
        raise SystemExit(f"source PBF does not exist: {source}")
    bootstrap_pbf = getattr(args, "bootstrap_pbf", None)
    if bootstrap_pbf is not None:
        bootstrap_pbf = bootstrap_pbf.resolve()
        if not bootstrap_pbf.is_file():
            raise SystemExit(f"bootstrap PBF does not exist: {bootstrap_pbf}")
    work.mkdir(parents=True, exist_ok=True)
    database.parent.mkdir(parents=True, exist_ok=True)
    database_build_dir.mkdir(parents=True, exist_ok=True)
    lock_path = work / "osm-map-update.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another map update is already running") from None
        lock.write(f"pid={os.getpid()} started={time.time()}\n")
        lock.flush()
        return _update_locked(
            args,
            source,
            database,
            work,
            database_build_dir,
            bootstrap_pbf=bootstrap_pbf,
        )


def _update_locked(
    args: argparse.Namespace,
    source: Path,
    database: Path,
    work: Path,
    database_build_dir: Path,
    *,
    bootstrap_pbf: Path | None = None,
) -> int:
    candidates = [work / "updated-a.osm.pbf", work / "updated-b.osm.pbf"]
    checkpoint = work / "updated-ready.osm.pbf"
    for path in candidates:
        _remove(path)
    if bootstrap_pbf is not None:
        _remove(checkpoint)
    current_input = bootstrap_pbf or (checkpoint if checkpoint.is_file() else source)
    updated: Path | None = None
    for attempt in range(args.max_update_batches):
        output = candidates[attempt % 2]
        _remove(output)
        command = [
            str(args.updater),
            str(current_input),
            "--outfile",
            str(output),
            "--tmpdir",
            str(work),
            "--size",
            str(args.diff_batch_mb),
            "--socket-timeout",
            str(args.socket_timeout),
        ]
        if args.server:
            command.extend(("--server", args.server))
        result = _run(command)
        if result not in {0, 1}:
            raise SystemExit(f"PBF update failed with exit code {result}")
        if not output.exists():
            if updated is None:
                if bootstrap_pbf is not None or current_input == checkpoint:
                    updated = current_input
                    break
                print("PBF is already current; database was not rebuilt", flush=True)
                return 0
            break
        previous_input, current_input, updated = current_input, output, output
        if previous_input in candidates and previous_input != updated:
            _remove(previous_input)
        if result == 0:
            break
    else:
        raise SystemExit("PBF update exceeded the configured batch limit")

    assert updated is not None
    if updated in candidates:
        os.replace(updated, checkpoint)
        updated = checkpoint
    build_database = database_build_dir / f"{database.name}.{os.getpid()}.building"
    staged_database = database.with_name(f".{database.name}.next")
    staged_source = source.with_name(f".{source.name}.next")
    for path in (build_database, staged_database, staged_source):
        _remove(path)

    build_command: list[str] = []
    if args.builder.suffix == ".py":
        build_command.append(str(args.builder_python))
    build_command.extend(
        [
            str(args.builder),
            str(updated),
            str(build_database),
            "--temp-dir",
            str(args.index_temp_dir),
            "--batch-size",
            str(args.build_batch_size),
        ]
    )
    try:
        result = _run(build_command)
        if result != 0:
            raise SystemExit(f"map database build failed with exit code {result}")
        _stage_file(build_database, staged_database)
        _stage_file(updated, staged_source)

        previous_source = source.with_name(f"{source.name}.previous")
        previous_database = database.with_name(f"{database.name}.previous")
        _rotate(source, staged_source, previous_source)
        try:
            _rotate(database, staged_database, previous_database)
        except Exception:
            _restore_previous(source, previous_source)
            raise
    finally:
        _remove(build_database)
        _remove(staged_database)
        _remove(staged_source)
    for path in candidates:
        _remove(path)
    _remove(checkpoint)

    print(f"updated_pbf={source}", flush=True)
    print(f"updated_database={database}", flush=True)
    print(f"rollback_pbf={previous_source}", flush=True)
    print(f"rollback_database={previous_database}", flush=True)
    return 0


def _parser() -> argparse.ArgumentParser:
    package_root = Path(__file__).resolve().parent
    compiled_builder = package_root / "bin" / "osm_map_builder"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--bootstrap-pbf",
        type=Path,
        help=(
            "use a complete replacement PBF as the update input, then atomically "
            "publish it at --source after the database build succeeds"
        ),
    )
    parser.add_argument(
        "--work-dir", type=Path, default=Path("/disk/dev/osm-map-rebuild")
    )
    parser.add_argument(
        "--database-build-dir",
        type=Path,
        default=Path("/disk/dev/osm-map-build"),
        help="large-disk directory used for the SQLite build before activation",
    )
    parser.add_argument(
        "--index-temp-dir", type=Path, default=Path("/disk/dev/osm-map-index")
    )
    parser.add_argument(
        "--updater",
        type=Path,
        default=Path("~/.local/bin/pyosmium-up-to-date").expanduser(),
    )
    parser.add_argument("--server", default="")
    parser.add_argument("--diff-batch-mb", type=int, default=256)
    parser.add_argument("--socket-timeout", type=int, default=120)
    parser.add_argument("--max-update-batches", type=int, default=16)
    parser.add_argument("--builder-python", type=Path, default=Path("/usr/bin/python3"))
    parser.add_argument(
        "--builder",
        type=Path,
        default=(
            compiled_builder
            if compiled_builder.is_file()
            else package_root / "build_index.py"
        ),
        help="compiled C++ builder executable or Python builder script",
    )
    parser.add_argument("--build-batch-size", type=int, default=50_000)
    return parser


if __name__ == "__main__":
    arguments = _parser().parse_args()
    arguments.diff_batch_mb = max(1, min(arguments.diff_batch_mb, 1024))
    arguments.socket_timeout = max(10, min(arguments.socket_timeout, 600))
    arguments.max_update_batches = max(1, min(arguments.max_update_batches, 100))
    arguments.build_batch_size = max(1_000, min(arguments.build_batch_size, 500_000))
    raise SystemExit(update(arguments))
