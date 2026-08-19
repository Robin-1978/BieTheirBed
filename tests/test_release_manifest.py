from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from knoa_platform.release import (
    ProtocolCompatibility,
    ReleaseArtifact,
    ReleaseManifest,
    ReleaseStore,
    ReleaseTrustStore,
    RuntimeExtensionDescriptor,
    SignedReleaseManifest,
    TargetPlatform,
    TrustedReleaseSigner,
    sign_manifest,
    verify_release,
)


def _write_artifact(root: Path, path: str, content: bytes, kind: str) -> ReleaseArtifact:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return ReleaseArtifact(
        path=path,
        kind=kind,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        executable=kind in {"python_runtime", "launcher", "runtime_extension_worker"},
    )


def _public_key_text(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _product_bundle(root: Path, release_id: str, key: Ed25519PrivateKey) -> None:
    artifacts = (
        _write_artifact(root, "runtime/python", b"python", "python_runtime"),
        _write_artifact(root, "app/knoa.whl", b"wheel", "application"),
        _write_artifact(root, "bin/knoa", b"launcher", "launcher"),
    )
    manifest = ReleaseManifest(
        release_id=release_id,
        version="1.2.3",
        release_kind="product",
        role="node",
        target=TargetPlatform(os="linux", arch="x86_64"),
        created_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        protocols=ProtocolCompatibility(),
        artifacts=artifacts,
    )
    (root / "release-manifest.json").write_text(
        sign_manifest(manifest, key, key_id="release-key-1").encoded(),
        encoding="utf-8",
    )


def _product_signers(key: Ed25519PrivateKey) -> dict[str, TrustedReleaseSigner]:
    return {
        "release-key-1": TrustedReleaseSigner(
            public_key=key.public_key(),
            allowed_release_kinds=frozenset({"product"}),
        )
    }


def test_signed_product_release_verifies_artifacts(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    _product_bundle(tmp_path, "knoa-node-1.2.3", key)
    signed = SignedReleaseManifest.model_validate_json(
        (tmp_path / "release-manifest.json").read_text(encoding="utf-8")
    )

    verify_release(
        signed,
        trusted_signers=_product_signers(key),
        payload_root=tmp_path,
    )

    (tmp_path / "app" / "knoa.whl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_release(
            signed,
            trusted_signers=_product_signers(key),
            payload_root=tmp_path,
        )


def test_runtime_extension_is_not_a_product_role(tmp_path: Path) -> None:
    worker = _write_artifact(
        tmp_path,
        "bin/acme-agent",
        b"worker",
        "runtime_extension_worker",
    )
    manifest = ReleaseManifest(
        release_id="acme-agent-1.0.0",
        version="1.0.0",
        release_kind="runtime_extension",
        target=TargetPlatform(os="windows", arch="x86_64"),
        created_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        artifacts=(worker,),
        extension=RuntimeExtensionDescriptor(
            extension_id="acme.agent",
            runtime_kind="acme_agent",
            display_name="Acme Agent",
            publisher="Acme",
            entrypoint=("bin/acme-agent", "worker"),
        ),
    )
    assert manifest.role is None

    invalid = manifest.model_dump()
    invalid["role"] = "node"
    with pytest.raises(ValidationError, match="forbids role"):
        ReleaseManifest(**invalid)


def test_runtime_extension_key_cannot_sign_product_release(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    _product_bundle(tmp_path, "product-release", key)
    signed = SignedReleaseManifest.model_validate_json(
        (tmp_path / "release-manifest.json").read_text(encoding="utf-8")
    )

    with pytest.raises(PermissionError, match="not trusted for this kind"):
        verify_release(
            signed,
            trusted_signers={
                "release-key-1": TrustedReleaseSigner(
                    public_key=key.public_key(),
                    allowed_release_kinds=frozenset({"runtime_extension"}),
                    allowed_extension_ids=frozenset({"acme.agent"}),
                )
            },
            payload_root=tmp_path,
        )


def test_release_artifact_rejects_unsafe_paths() -> None:
    with pytest.raises(ValidationError, match="safe POSIX"):
        ReleaseArtifact(
            path="../outside",
            kind="application",
            size=0,
            sha256="0" * 64,
        )


def test_release_store_activates_and_rolls_back(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    bundle_one = tmp_path / "bundle-one"
    bundle_two = tmp_path / "bundle-two"
    bundle_one.mkdir()
    bundle_two.mkdir()
    _product_bundle(bundle_one, "release-one", key)
    _product_bundle(bundle_two, "release-two", key)
    store = ReleaseStore(
        tmp_path / "store",
        trusted_signers=_product_signers(key),
        expected_release_kind="product",
        expected_target=TargetPlatform(os="linux", arch="x86_64"),
        expected_role="node",
        health_check=lambda candidate: (candidate / "bin" / "knoa").read_bytes(),
    )

    first = store.install(bundle_one)
    second = store.install(bundle_two)
    rolled_back = store.rollback()

    assert first.previous_release_id == ""
    assert second.previous_release_id == "release-one"
    assert rolled_back.release_id == "release-one"
    assert rolled_back.rolled_back is True
    assert store.current_path() == tmp_path / "store" / "versions" / "release-one"


def test_release_store_restores_pointer_after_failed_health(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    _product_bundle(good, "good-release", key)
    _product_bundle(bad, "bad-release", key)

    def health(candidate: Path) -> None:
        if candidate.name == "bad-release":
            raise RuntimeError("unhealthy")

    store = ReleaseStore(
        tmp_path / "store",
        trusted_signers=_product_signers(key),
        expected_release_kind="product",
        expected_target=TargetPlatform(os="linux", arch="x86_64"),
        expected_role="node",
        health_check=health,
    )
    store.install(good)
    with pytest.raises(RuntimeError, match="unhealthy"):
        store.install(bad)

    assert store.current_release_id() == "good-release"


def test_release_store_rejects_wrong_target_before_activation(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _product_bundle(bundle, "linux-release", key)
    store = ReleaseStore(
        tmp_path / "store",
        trusted_signers=_product_signers(key),
        expected_release_kind="product",
        expected_target=TargetPlatform(os="windows", arch="x86_64"),
        expected_role="node",
        health_check=lambda _candidate: None,
    )

    with pytest.raises(ValueError, match="target does not match"):
        store.install(bundle)
    assert store.current_release_id() == ""


def test_release_verification_rejects_undeclared_files(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    _product_bundle(tmp_path, "product-release", key)
    signed = SignedReleaseManifest.model_validate_json(
        (tmp_path / "release-manifest.json").read_text(encoding="utf-8")
    )
    (tmp_path / "unexpected.txt").write_text("not signed", encoding="utf-8")

    with pytest.raises(ValueError, match="inventory mismatch"):
        verify_release(
            signed,
            trusted_signers=_product_signers(key),
            payload_root=tmp_path,
        )


def test_release_trust_store_preserves_signing_domains() -> None:
    product_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    extension_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    store = ReleaseTrustStore.model_validate_json(
        json.dumps(
            {
            "schema_version": 1,
            "keys": [
                {
                    "key_id": "knoa-product",
                    "public_key": _public_key_text(product_key),
                    "allowed_release_kinds": ["product"],
                },
                {
                    "key_id": "acme-runtime",
                    "public_key": _public_key_text(extension_key),
                    "allowed_release_kinds": ["runtime_extension"],
                    "allowed_extension_ids": ["acme.agent"],
                },
            ],
            }
        )
    )

    signers = store.trusted_signers()
    assert signers["knoa-product"].allowed_release_kinds == frozenset({"product"})
    assert signers["acme-runtime"].allowed_extension_ids == frozenset(
        {"acme.agent"}
    )


def test_cross_language_release_fixture_verifies_in_python() -> None:
    root = (
        Path(__file__).resolve().parents[1]
        / "protocol"
        / "fixtures"
        / "release-v1"
    )
    signed = SignedReleaseManifest.model_validate_json(
        (root / "product-node-linux" / "release-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    trust = ReleaseTrustStore.model_validate_json(
        (root / "trust-store.json").read_text(encoding="utf-8")
    )

    verify_release(
        signed,
        trusted_signers=trust.trusted_signers(),
        payload_root=root / "product-node-linux",
    )
