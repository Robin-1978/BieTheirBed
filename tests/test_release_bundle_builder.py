from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knoa_platform.release import (
    TrustedReleaseSigner,
    load_signed_manifest,
    verify_release,
)
from scripts.build_release_bundle import build_bundle


def _payload(root: Path) -> Path:
    payload = root / "payload"
    for relative, content in {
        "runtime/bin/python3": b"python",
        "app/site-packages/knoa.py": b"app",
        "bin/knoa-node": b"launcher",
    }.items():
        path = payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if relative in {"runtime/bin/python3", "bin/knoa-node"}:
            path.chmod(0o755)
    return payload


def test_builder_creates_self_verifying_product_bundle(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    output = tmp_path / "output"
    manifest = build_bundle(
        payload=_payload(tmp_path),
        output=output,
        release_kind="product",
        role="node",
        target_os="linux",
        target_arch="x86_64",
        version="2.0.0",
        release_id="node-linux-2.0.0",
        signing_key=key,
        key_id="test-key",
    )

    assert {artifact.kind for artifact in manifest.artifacts} == {
        "application",
        "python_runtime",
        "launcher",
    }
    signed = load_signed_manifest(output / "release-manifest.json")
    verify_release(
        signed,
        trusted_signers={
            "test-key": TrustedReleaseSigner(
                public_key=key.public_key(),
                allowed_release_kinds=frozenset({"product"}),
            )
        },
        payload_root=output,
    )


def test_builder_never_overwrites_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "owned-by-user").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        build_bundle(
            payload=_payload(tmp_path),
            output=output,
            release_kind="product",
            role="node",
            target_os="linux",
            target_arch="x86_64",
            version="2.0.0",
            release_id="node-linux-2.0.0",
            signing_key=Ed25519PrivateKey.generate(),
            key_id="test-key",
        )
