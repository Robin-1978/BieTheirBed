from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knoa_platform.extensions.capability_catalog import (
    CapabilityCatalogService,
    OFFICIAL_CATALOG_TRUST_ROOTS,
)
from knoa_platform.extensions.package_store import PackageStore


class _Installer:
    async def prepare(self, _principal_id, _source):
        return SimpleNamespace(
            package_digest="26a28c75d9093723443fee19841eab41f471c551f69d7ff4ed8c3dca4d524239",
            version="1.0.2",
            capability_id="browser",
        )


@pytest.mark.asyncio
async def test_official_catalog_is_signed_and_browser_digest_matches_reference_package(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    packages = PackageStore(tmp_path / "packages")
    service = CapabilityCatalogService(
        root / "catalog/capabilities.json",
        trust_roots=OFFICIAL_CATALOG_TRUST_ROOTS,
        source_root=root,
        database=tmp_path / "gateway.db",
        packages=packages,
        installer=_Installer(),  # type: ignore[arg-type]
    )
    entry = service.resolve("knoa.browser")
    package = packages.import_directory("capability", service.source_path(entry), imported_by="principal-a")
    assert package.content_digest == entry.package_digest
    assert service.select("principal-a", "knoa.browser", mode="pinned", version="1.0.2")["resolved_version"] == "1.0.2"
    plan = await service.prepare("principal-a", "knoa.browser")
    assert plan.capability_id == "browser"


def test_catalog_rejects_tampering_and_revoked_versions(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )).rstrip(b"=").decode()
    payload = {
        "schema": "knoa-capability-catalog-v1", "catalog_id": "test", "generated_at": 1,
        "entries": [{
            "id": "demo", "version": "1.0.0", "display_name": "Demo",
            "description": "Demo package", "platform": ">=0.2.0",
            "operating_systems": [], "architectures": [], "package_digest": "0" * 64,
            "source": "relative://demo", "permission_summary": [],
            "revoked": True, "revocation_severity": "critical",
        }],
    }
    transcript = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["signature"] = {
        "key_id": "test-key",
        "value": base64.urlsafe_b64encode(key.sign(transcript)).rstrip(b"=").decode(),
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    service = CapabilityCatalogService(
        catalog, trust_roots={"test-key": public}, source_root=tmp_path,
        database=tmp_path / "gateway.db", packages=PackageStore(tmp_path / "packages"),
        installer=_Installer(),  # type: ignore[arg-type]
    )
    with pytest.raises(PermissionError, match="Revoked"):
        service.resolve("demo")
    payload["entries"][0]["description"] = "tampered"
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PermissionError, match="signature"):
        service.load()


def test_sdist_contains_catalog_and_browser_reference_package() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert '"/catalog/capabilities.json"' in pyproject
    assert '"/examples/browser_mcp_server"' in pyproject
