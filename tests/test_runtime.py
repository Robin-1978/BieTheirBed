from __future__ import annotations

from pc_assistant.harness.audit import AuditLogger
from pc_assistant.config import load_config
from pc_assistant.runtime import RuntimePaths, default_runtime_root


def test_runtime_layout_has_sibling_directories(tmp_path):
    paths = RuntimePaths.from_root(tmp_path)
    assert paths.logs.parent == paths.root
    assert paths.attachments.parent == paths.root
    assert paths.cache.parent == paths.root
    assert len({paths.logs, paths.attachments, paths.cache}) == 3
    assert paths.data.parent == paths.root


def test_default_runtime_root_is_below_user_home(monkeypatch, tmp_path):
    monkeypatch.delenv("PC_ASSISTANT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_runtime_root() == tmp_path / ".pc-assistant"


def test_runtime_root_can_be_overridden(monkeypatch, tmp_path):
    override = tmp_path / "state"
    monkeypatch.setenv("PC_ASSISTANT_HOME", str(override))
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


def test_relative_log_resolves_below_runtime_root(tmp_path):
    paths = RuntimePaths.from_root(tmp_path)
    assert paths.resolve("logs/app.json") == tmp_path / "logs" / "app.json"


def test_audit_redacts_secrets_and_binary_images(tmp_path):
    audit = AuditLogger(str(tmp_path / "logs" / "audit"))
    audit.log(
        "tool_call",
        parameters={
            "api_key": "secret-value",
            "nested": {"password": "pw"},
            "image": "data:image/jpeg;base64,AAAA",
        },
    )
    parameters = audit.get_entries()[0]["parameters"]
    assert parameters["api_key"] == "[redacted]"
    assert parameters["nested"]["password"] == "[redacted]"
    assert "AAAA" not in parameters["image"]
