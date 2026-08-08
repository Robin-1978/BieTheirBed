from __future__ import annotations

from pc_assistant.config import load_config
from pc_assistant.runtime import RuntimePaths, default_runtime_root


def test_runtime_layout_has_sibling_directories(tmp_path):
    paths = RuntimePaths.from_root(tmp_path)
    assert paths.logs.parent == paths.root
    assert paths.attachments.parent == paths.root
    assert paths.artifacts.parent == paths.root
    assert paths.cache.parent == paths.root
    assert len({paths.logs, paths.attachments, paths.artifacts, paths.cache}) == 4
    assert paths.data.parent == paths.root
    assert not hasattr(paths, "socket")


def test_default_runtime_root_is_below_user_home(monkeypatch, tmp_path):
    monkeypatch.delenv("PC_ASSISTANT_HOME", raising=False)
    monkeypatch.delenv("PC_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_runtime_root() == tmp_path / ".pc-assistant"


def test_runtime_root_can_be_overridden(monkeypatch, tmp_path):
    override = tmp_path / "state"
    monkeypatch.setenv("PC_ASSISTANT_HOME", str(override))
    monkeypatch.delenv("PC_RUNTIME_ROOT", raising=False)
    assert RuntimePaths.from_root().root == override


def test_assistant_home_overrides_default_yaml(monkeypatch, tmp_path):
    override = tmp_path / "state"
    monkeypatch.setenv("PC_ASSISTANT_HOME", str(override))
    monkeypatch.delenv("PC_RUNTIME_ROOT", raising=False)
    assert load_config().runtime_root == str(override)


def test_runtime_root_env_wins_over_assistant_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PC_ASSISTANT_HOME", str(tmp_path / "friendly"))
    monkeypatch.setenv("PC_RUNTIME_ROOT", str(tmp_path / "specific"))
    assert load_config().runtime_root == str(tmp_path / "specific")


def test_runtime_root_env_selects_persistent_local_config(monkeypatch, tmp_path):
    runtime_root = tmp_path / "specific"
    local_config = runtime_root / "config" / "local.yaml"
    local_config.parent.mkdir(parents=True)
    local_config.write_text("max_iterations: 17\n", encoding="utf-8")
    monkeypatch.setenv("PC_RUNTIME_ROOT", str(runtime_root))

    assert default_runtime_root() == runtime_root
    assert load_config().max_iterations == 17


def test_relative_log_resolves_below_runtime_root(tmp_path):
    paths = RuntimePaths.from_root(tmp_path)
    assert paths.resolve("logs/app.json") == tmp_path / "logs" / "app.json"
