from __future__ import annotations

from pc_assistant.harness.audit import AuditLogger
from pc_assistant.runtime import RuntimePaths


def test_runtime_layout_has_sibling_directories(tmp_path):
    paths = RuntimePaths.from_root(tmp_path)
    assert paths.logs.parent == paths.root
    assert paths.attachments.parent == paths.root
    assert paths.cache.parent == paths.root
    assert len({paths.logs, paths.attachments, paths.cache}) == 3


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
