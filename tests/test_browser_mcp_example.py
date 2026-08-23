from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from knoa_platform.config import AppConfig
from knoa_platform.configuration import ConfigRegistry, ConfigurationService
from knoa_platform.extensions.capability_bundle import (
    CapabilityInstallationRepository,
    CapabilityInstaller,
    load_capability_bundle,
)
from knoa_platform.extensions.package_store import PackageStore


ROOT = Path("examples/browser_mcp_server").resolve()


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_browser_session_navigation_snapshot_download_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        """<!doctype html><title>Reference page</title>
        <h1>Untrusted reference evidence</h1>
        <label>Name <input aria-label='Name'></label>
        <button onclick=\"this.textContent='Clicked'\">Continue</button>
        <a href='/evidence.txt'>Evidence</a>""",
        encoding="utf-8",
    )
    (site / "evidence.txt").write_text("bounded browser evidence", encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(site), **kwargs)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("KNOA_BROWSER_ALLOW_PRIVATE_ORIGINS", origin)
    monkeypatch.setenv("KNOA_BROWSER_STATE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("KNOA_BROWSER_DOWNLOAD_ROOT", str(tmp_path / "downloads"))
    browser = _module("browser_reference_client", "browser_client.py")
    manager = browser.BrowserManager()
    opened = await manager.open()
    session_id = opened["browser_session_id"]
    try:
        navigated = await manager.navigate(session_id, f"{origin}/index.html")
        assert navigated["url"].endswith("/index.html")
        snapshot = await manager.snapshot(session_id)
        assert snapshot["untrusted_page_content"] is True
        assert any(item["name"] == "Continue" for item in snapshot["nodes"])
        assert len(snapshot["nodes"]) <= browser.MAX_SNAPSHOT_NODES
        screenshot = await manager.screenshot(session_id)
        assert screenshot["managed_file"]["media_type"] == "image/png"
        downloaded = await manager.download(
            session_id, f"{origin}/evidence.txt", "evidence.txt",
        )
        assert downloaded["managed_file"]["size_bytes"] == len("bounded browser evidence")
        assert len(downloaded["managed_file"]["sha256"]) == 64
    finally:
        await manager.close(session_id)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not (tmp_path / "downloads" / session_id).exists()
    assert not tuple((tmp_path / "profiles").glob("session-*"))


@pytest.mark.asyncio
async def test_browser_mcp_shutdown_reaps_chromium_profiles_and_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOA_BROWSER_STATE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("KNOA_BROWSER_DOWNLOAD_ROOT", str(tmp_path / "downloads"))
    browser = _module("browser_reference_client_shutdown", "browser_client.py")
    manager = browser.BrowserManager()
    opened = await manager.open()
    session = manager.get(opened["browser_session_id"])
    process = session.process
    profile = session.profile
    downloads = session.download_directory

    await manager.shutdown()

    assert process.returncode is not None
    assert not profile.exists()
    assert not downloads.exists()
    assert manager.sessions == {}


def test_browser_rejects_local_metadata_and_dangerous_schemes() -> None:
    browser = _module("browser_reference_client_safety", "browser_client.py")
    for url in (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:password@example.com/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
    ):
        with pytest.raises(ValueError):
            browser._safe_url(url, frozenset())


@pytest.mark.asyncio
async def test_browser_installs_and_disables_through_generic_capability_transaction(
    tmp_path: Path,
) -> None:
    class Port:
        def __init__(self, service):
            self.service = service

        async def get_config_current(self, _principal):
            return self.service.current(), self.service.state(), ()

        async def create_config_draft(self, principal):
            return self.service.create_draft(actor=principal)

        async def replace_config_draft(self, principal, draft_id, document, *, expected_version):
            return self.service.replace_draft(
                draft_id, document, expected_version=expected_version, actor=principal,
            )

        async def validate_config_draft(self, _principal, draft_id, *, preflight=False):
            return await (
                self.service.preflight(draft_id)
                if preflight else self.service.validate(draft_id)
            )

        async def publish_config_draft(self, principal, draft_id, *, expected_version, summary=""):
            return await self.service.publish(
                draft_id, expected_version=expected_version,
                actor=principal, summary=summary,
            )

        async def rollback_config(self, principal, revision_id, *, summary=""):
            return await self.service.rollback(
                revision_id, actor=principal, summary=summary,
            )

    manifest, _ = load_capability_bundle(ROOT)
    assert manifest.id == "browser"
    configuration = ConfigurationService(
        ConfigRegistry(tmp_path / "config.db"),
        AppConfig().managed_config(),
        bootstrap_actor="bootstrap",
    )
    installer = CapabilityInstaller(
        PackageStore(tmp_path / "packages"),
        Port(configuration),
        CapabilityInstallationRepository(tmp_path / "gateway.db"),
    )
    plan = await installer.prepare("personal:owner", ROOT)
    assert plan.withheld_tools == ()
    assert plan.component_packages.keys() == {"mcp:browser_mcp_server"}
    with pytest.raises(ValueError, match="confirmation"):
        await installer.confirm("personal:owner", plan.operation_id, "0" * 64)
    installed = await installer.confirm(
        "personal:owner", plan.operation_id, plan.plan_digest,
    )
    configured = configuration.current().document.mcp_servers["browser_mcp_server"]
    assert installed.health == "healthy"
    assert configured.enabled is True
    assert set(configured.tools) == {item.name for item in plan.requested_tools}

    disabled = await installer.set_enabled("personal:owner", "browser", False)
    assert disabled.health == "disabled"
    assert configuration.current().document.mcp_servers[
        "browser_mcp_server"
    ].enabled is False
