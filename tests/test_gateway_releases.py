from __future__ import annotations

import hashlib
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from pc_assistant.config import AppConfig
from pc_assistant.gateway.adapter import SecureGatewayAdapter
from pc_assistant.gateway.releases import AndroidReleaseRepository


def _apk(path, payload: bytes = b"classes") -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", payload)
    return path.read_bytes()


def test_android_release_repository_publishes_immutable_versions(tmp_path) -> None:
    repository = AndroidReleaseRepository(tmp_path / "releases")
    apk = tmp_path / "knoa.apk"
    _apk(apk, b"APK-v1" * 100)

    release = repository.publish(
        apk,
        version_name="0.2.0",
        version_code=2,
        release_notes="断点续传",
        clock=lambda: 123.0,
    )

    assert release.sha256 == hashlib.sha256(apk.read_bytes()).hexdigest()
    assert release.published_at == 123.0
    assert repository.latest() == release
    assert repository.get(2) == release
    assert repository.package_path(release).read_bytes() == apk.read_bytes()
    assert repository.root.stat().st_mode & 0o777 == 0o700
    assert repository.package_path(release).stat().st_mode & 0o777 == 0o600

    with pytest.raises(ValueError, match="increase monotonically"):
        repository.publish(apk, version_name="0.2.1", version_code=2)


def test_android_release_repository_rejects_symlink_and_invalid_minimum(
    tmp_path,
) -> None:
    repository = AndroidReleaseRepository(tmp_path / "releases")
    apk = tmp_path / "knoa.apk"
    _apk(apk)
    link = tmp_path / "linked.apk"
    link.symlink_to(apk)

    with pytest.raises(ValueError, match="non-symlink"):
        repository.publish(link, version_name="1.0.0", version_code=1)
    with pytest.raises(ValueError, match="Minimum supported"):
        repository.publish(
            apk,
            version_name="1.0.0",
            version_code=1,
            min_supported_version_code=2,
        )


class _Authentication:
    def authenticate_session(self, token: str):
        if token != "valid-session":
            from pc_assistant.gateway.auth import GatewayAuthenticationRejectedError

            raise GatewayAuthenticationRejectedError("invalid")
        return SimpleNamespace(
            session_id="gws-a",
            expires_at=999.0,
            device=SimpleNamespace(
                device_id="dev-a",
                principal_id="personal:owner",
            ),
        )


@pytest.mark.asyncio
async def test_gateway_android_release_uses_public_immutable_byte_ranges(
    tmp_path,
) -> None:
    repository = AndroidReleaseRepository(tmp_path / "releases")
    apk = tmp_path / "knoa.apk"
    payload = _apk(apk, bytes(range(256)) * 8)
    release = repository.publish(apk, version_name="0.2.0", version_code=2)
    adapter = SecureGatewayAdapter(
        AppConfig(
            fallback_enabled=False,
            runtime_root=str(tmp_path),
            gateway_enabled=True,
            gateway_port=0,
        ),
        authentication=_Authentication(),
        release_repository=repository,
    )
    transport = httpx.ASGITransport(app=adapter.app)
    headers = {"Authorization": "Bearer valid-session"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway.local"
    ) as http:
        missing_auth = await http.get("/v1/mobile/releases/android/latest")
        latest = await http.get("/v1/mobile/releases/android/latest", headers=headers)
        ranged = await http.get(
            latest.json()["download_path"],
            headers={"Range": "bytes=100-299"},
        )
        wrong_digest = await http.get(
            f"/releases/android/2/{'0' * 64}/knoa.apk"
        )

    assert missing_auth.status_code == 401
    assert latest.status_code == 200
    assert latest.json()["version_code"] == 2
    assert latest.json()["sha256"] == release.sha256
    assert ranged.status_code == 206
    assert ranged.content == payload[100:300]
    assert ranged.headers["content-range"] == f"bytes 100-299/{len(payload)}"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["etag"] == f'"{release.sha256}"'
    assert ranged.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert ranged.headers["x-content-type-options"] == "nosniff"
    assert wrong_digest.status_code == 404
