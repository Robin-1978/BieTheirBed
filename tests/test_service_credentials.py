from __future__ import annotations

import stat

import pytest

from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.credentials import resolve_local_service_token


def test_managed_service_token_is_persistent_and_private(tmp_path) -> None:
    paths = RuntimePaths.from_root(tmp_path)

    first = resolve_local_service_token(paths)
    second = resolve_local_service_token(paths)
    token_path = paths.config / "service.token"

    assert first == second
    assert len(first) >= 32
    assert stat.S_IMODE(paths.config.stat().st_mode) == 0o700
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_managed_service_token_rejects_non_private_file(tmp_path) -> None:
    paths = RuntimePaths.from_root(tmp_path)
    paths.config.mkdir(parents=True)
    token_path = paths.config / "service.token"
    token_path.write_text("exposed", encoding="utf-8")
    token_path.chmod(0o644)

    with pytest.raises(RuntimeError, match="owner-only"):
        resolve_local_service_token(paths)
