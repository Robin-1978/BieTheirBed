"""The OpenCode background service of the current OS user registers itself in
``$XDG_STATE_HOME/opencode/service.json`` (URL, pid and Basic-Auth password).
KNOA follows that registration instead of asking for a password."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from knoa_codex_agent import OpenCodeEndpoint
from knoa_platform.opencode_service import (
    OpenCodeServiceLocator,
    OpenCodeServiceRecordError,
    default_record_path,
)

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="record ownership and permission bits are POSIX only"
)


def _write_record(
    path: Path,
    *,
    url: str = "http://127.0.0.1:4096",
    pid: int | None = None,
    password: str = "pw-1",
    mode: int = 0o600,
    **extra: object,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": "svc-1",
        "version": "2.2.2",
        "url": url,
        "pid": os.getpid() if pid is None else pid,
        "password": password,
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(mode)
    return path


def test_locator_reads_the_registration_of_a_running_service(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "opencode" / "service.json")

    endpoint = OpenCodeServiceLocator(record).locate()

    assert endpoint == OpenCodeEndpoint(
        base_url="http://127.0.0.1:4096",
        password="pw-1",
        username="opencode",
        pid=os.getpid(),
    )


def test_locator_reports_no_service_when_nothing_is_registered(tmp_path: Path) -> None:
    assert OpenCodeServiceLocator(tmp_path / "missing" / "service.json").locate() is None


def test_locator_reports_no_service_when_the_recorded_process_is_gone(
    tmp_path: Path,
) -> None:
    record = _write_record(tmp_path / "service.json", pid=4242)

    locator = OpenCodeServiceLocator(record, pid_alive=lambda pid: False)

    assert locator.locate() is None


def test_locator_notices_a_finished_process_with_the_default_probe(
    tmp_path: Path,
) -> None:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    record = _write_record(tmp_path / "service.json", pid=finished.pid)

    assert OpenCodeServiceLocator(record).locate() is None


def test_locator_follows_a_service_restart(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "service.json", password="old-pw")
    locator = OpenCodeServiceLocator(record)
    first = locator.locate()

    _write_record(record, url="http://127.0.0.1:50001", password="new-pw")
    second = locator.locate()

    assert first is not None and second is not None
    assert (first.base_url, first.password) == ("http://127.0.0.1:4096", "old-pw")
    assert (second.base_url, second.password) == ("http://127.0.0.1:50001", "new-pw")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:4096", "http://127.0.0.1:4096"),
        ("http://127.0.0.1:4096/", "http://127.0.0.1:4096"),
        ("http://localhost:4096", "http://localhost:4096"),
        ("http://[::1]:4096", "http://[::1]:4096"),
    ],
)
def test_locator_accepts_loopback_http_endpoints(
    tmp_path: Path, url: str, expected: str
) -> None:
    record = _write_record(tmp_path / "service.json", url=url)

    endpoint = OpenCodeServiceLocator(record).locate()

    assert endpoint is not None
    assert endpoint.base_url == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:4096",
        "http://example.com:4096",
        "https://127.0.0.1:4096",
        "ftp://127.0.0.1:21",
        "http://user:secret@127.0.0.1:4096",
        "http://127.0.0.1:4096/api",
        "http://127.0.0.1:4096?x=1",
        "127.0.0.1:4096",
        "",
    ],
)
def test_locator_refuses_endpoints_that_are_not_plain_loopback_http(
    tmp_path: Path, url: str
) -> None:
    record = _write_record(tmp_path / "service.json", url=url)

    with pytest.raises(OpenCodeServiceRecordError, match="loopback"):
        OpenCodeServiceLocator(record).locate()


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"url": "http://127.0.0.1:1", "pid": 1}',
        '{"url": "http://127.0.0.1:1", "pid": 1, "password": ""}',
        '{"url": "http://127.0.0.1:1", "pid": "1", "password": "pw"}',
        '{"url": "http://127.0.0.1:1", "pid": true, "password": "pw"}',
        '{"url": "http://127.0.0.1:1", "pid": 0, "password": "pw"}',
        '{"url": "http://127.0.0.1:1", "pid": 7, "password": 12345}',
    ],
)
def test_locator_rejects_incomplete_or_corrupt_records(
    tmp_path: Path, payload: str
) -> None:
    record = tmp_path / "service.json"
    record.write_text(payload, encoding="utf-8")
    record.chmod(0o600)

    with pytest.raises(OpenCodeServiceRecordError):
        OpenCodeServiceLocator(record, pid_alive=lambda pid: True).locate()


def test_locator_rejects_an_oversized_record(tmp_path: Path) -> None:
    record = tmp_path / "service.json"
    record.write_text("x" * 70_000, encoding="utf-8")
    record.chmod(0o600)

    with pytest.raises(OpenCodeServiceRecordError, match="too large"):
        OpenCodeServiceLocator(record).locate()


@POSIX_ONLY
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o604, 0o666])
def test_locator_refuses_a_record_other_users_can_access(
    tmp_path: Path, mode: int
) -> None:
    record = _write_record(tmp_path / "service.json", mode=mode)

    with pytest.raises(OpenCodeServiceRecordError, match="private"):
        OpenCodeServiceLocator(record).locate()


@POSIX_ONLY
def test_locator_refuses_a_record_owned_by_another_user(tmp_path: Path) -> None:
    record = _write_record(tmp_path / "service.json")

    with pytest.raises(OpenCodeServiceRecordError, match="owned"):
        OpenCodeServiceLocator(record, uid=os.getuid() + 1).locate()


@POSIX_ONLY
def test_locator_refuses_a_symlinked_record(tmp_path: Path) -> None:
    real = _write_record(tmp_path / "real.json")
    link = tmp_path / "service.json"
    link.symlink_to(real)

    with pytest.raises(OpenCodeServiceRecordError, match="symlink"):
        OpenCodeServiceLocator(link).locate()


def test_locator_refuses_a_record_that_is_not_a_regular_file(tmp_path: Path) -> None:
    record = tmp_path / "service.json"
    record.mkdir()

    with pytest.raises(OpenCodeServiceRecordError, match="regular file"):
        OpenCodeServiceLocator(record).locate()


def test_record_errors_never_echo_the_secret(tmp_path: Path) -> None:
    record = _write_record(
        tmp_path / "service.json", url="http://10.0.0.5:1", password="super-secret-pw"
    )

    with pytest.raises(OpenCodeServiceRecordError) as info:
        OpenCodeServiceLocator(record).locate()

    assert "super-secret-pw" not in str(info.value)


def test_endpoint_never_shows_its_password() -> None:
    endpoint = OpenCodeEndpoint("http://127.0.0.1:1", "super-secret-pw")

    assert "super-secret-pw" not in repr(endpoint)
    assert "super-secret-pw" not in str(endpoint)


def test_default_record_path_follows_xdg_state_home(tmp_path: Path) -> None:
    assert default_record_path({"XDG_STATE_HOME": str(tmp_path)}) == (
        tmp_path / "opencode" / "service.json"
    )


def test_default_record_path_falls_back_to_the_home_state_directory(
    tmp_path: Path,
) -> None:
    expected = tmp_path / ".local" / "state" / "opencode" / "service.json"

    assert default_record_path({}, home=tmp_path) == expected
    assert default_record_path({"XDG_STATE_HOME": ""}, home=tmp_path) == expected
