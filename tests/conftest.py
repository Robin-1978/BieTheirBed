from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_root(tmp_path, monkeypatch):
    """Never let tests write application state into the real user home."""
    runtime_root = tmp_path / ".pc-assistant"
    monkeypatch.setenv("PC_ASSISTANT_HOME", str(runtime_root))
    monkeypatch.setenv("PC_RUNTIME_ROOT", str(runtime_root))
    return runtime_root


@pytest.fixture
def tmp_config(tmp_path):
    from pc_assistant.config import AppConfig

    return AppConfig(runtime_root=str(tmp_path / ".pc-assistant"))
