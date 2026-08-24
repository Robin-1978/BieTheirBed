from __future__ import annotations

import gzip
import logging

import pytest

from knoa_platform.log_rotation import (
    compressed_generations,
    compressed_rotating_file_handler,
    rotate_compressed_file,
)


def test_service_log_rotation_is_bounded_and_compressed(tmp_path) -> None:
    log_path = tmp_path / "service.log"
    handler = compressed_rotating_file_handler(
        log_path,
        max_bytes=80,
        backup_count=2,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.Logger("rotation-test")
    logger.addHandler(handler)

    for index in range(20):
        logger.info("entry-%02d-%s", index, "x" * 30)
    handler.close()

    backups = sorted(tmp_path.glob("service.log.*.gz"))
    assert backups == [
        tmp_path / "service.log.1.gz",
        tmp_path / "service.log.2.gz",
    ]
    assert log_path.stat().st_size < 80
    assert all(
        "entry-" in gzip.open(path, "rt", encoding="utf-8").read() for path in backups
    )


def test_service_log_rotation_requires_positive_limits(tmp_path) -> None:
    with pytest.raises(ValueError, match="positive"):
        compressed_rotating_file_handler(
            tmp_path / "service.log",
            max_bytes=0,
        )


def test_append_only_trace_rotation_retains_compressed_generations(tmp_path) -> None:
    trace_path = tmp_path / "turn_trace.jsonl"
    trace_path.write_text("old-generation\n", encoding="utf-8")

    rotate_compressed_file(
        trace_path,
        incoming_bytes=10,
        max_bytes=10,
        backup_count=2,
    )
    trace_path.write_text("current\n", encoding="utf-8")

    generations = compressed_generations(trace_path, backup_count=2)
    assert generations == (tmp_path / "turn_trace.jsonl.1.gz", trace_path)
    assert (
        gzip.open(generations[0], "rt", encoding="utf-8").read() == "old-generation\n"
    )
