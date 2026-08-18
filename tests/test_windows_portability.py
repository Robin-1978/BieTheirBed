from __future__ import annotations

import asyncio

import pytest

from knoa_platform import private_files, runtime
from knoa_platform.hub.service import HubService
from knoa_platform.node_hub import NodeHubStore
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.runtime import RuntimePaths
from knoa_platform.service import processes
from knoa_platform.service.credentials import resolve_local_service_token
from knoa_platform.service.shutdown import wait_for_shutdown


def test_windows_default_runtime_paths_are_node_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "IS_WINDOWS", True)
    monkeypatch.delenv("KNOA_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KNOA_HOME", raising=False)
    monkeypatch.delenv("KNOA_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    root = tmp_path / "Knoa" / "Node"
    paths = RuntimePaths.from_root()
    assert runtime.default_runtime_root() == root
    assert paths.pid == root / "run" / "service.pid"
    assert paths.stop_request == root / "run" / "service.stop"


def test_windows_private_state_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(private_files, "IS_WINDOWS", True)

    signing_key = tmp_path / "hub" / "hub-signing.key"
    first_hub_key = HubService._load_or_create_key(signing_key)
    second_hub_key = HubService._load_or_create_key(signing_key)
    assert first_hub_key.private_bytes_raw() == second_hub_key.private_bytes_raw()

    identity_store = NodeIdentityStore(tmp_path / "node" / "node-identity.json")
    first_identity = identity_store.load_or_create()
    second_identity = identity_store.load_or_create()
    assert first_identity.node_id == second_identity.node_id

    enrollment_store = NodeHubStore(tmp_path / "node" / "node-hub.json")
    enrollment_store.save(
        hub_url="https://hub.example.test/workspaces/ws_abcdefghijkl",
        hub_id="hub_example",
        hub_signing_public_key=first_identity.signing_public_key,
    )
    enrollment = enrollment_store.load()
    assert enrollment is not None
    assert enrollment.hub_id == "hub_example"

    paths = RuntimePaths.from_root(tmp_path / "node")
    assert resolve_local_service_token(paths) == resolve_local_service_token(paths)


@pytest.mark.asyncio
async def test_stop_request_ends_foreground_service_wait(tmp_path) -> None:
    stop_request = tmp_path / "run" / "service.stop"
    waiter = asyncio.create_task(wait_for_shutdown(stop_request))
    await asyncio.sleep(0)
    stop_request.touch()

    await asyncio.wait_for(waiter, timeout=2)
    assert not stop_request.exists()


def test_windows_process_probe_uses_read_only_native_query(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(processes, "IS_WINDOWS", True)
    monkeypatch.setattr(
        processes,
        "_windows_process_exists",
        lambda pid: not calls.append(pid),
    )

    assert processes.process_exists(42) is True
    assert calls == [42]
