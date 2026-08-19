from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knoa_platform.release import (
    ReleaseTrustKey,
    ReleaseTrustStore,
    extract_bundle,
    load_signed_manifest,
    verify_release,
)
from scripts.build_product_release import build_product_release


def test_one_command_product_builder_creates_verified_archive(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    python = runtime / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"embedded-python")
    python.chmod(0o755)
    application = tmp_path / "application"
    package = application / "knoa_platform"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '1.0.0'\n", encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "release-key.pem"
    private_key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    archive = build_product_release(
        role="all",
        target_os="linux",
        target_arch="x86_64",
        runtime_source=runtime,
        application_source=application,
        output_directory=tmp_path / "releases",
        signing_key_path=private_key_path,
        key_id="product-key",
        version="3.0.0",
    )

    assert archive.name == "knoa-host-3.0.0-linux-x86_64.zip"
    extracted = tmp_path / "extracted"
    extract_bundle(archive, extracted)
    signed = load_signed_manifest(extracted / "release-manifest.json")
    trust = ReleaseTrustStore(
        keys=(
            ReleaseTrustKey(
                key_id="product-key",
                public_key=(
                    base64.urlsafe_b64encode(key.public_key().public_bytes_raw())
                    .decode("ascii")
                    .rstrip("=")
                ),
                allowed_release_kinds=frozenset({"product"}),
            ),
        )
    )
    verify_release(
        signed,
        trusted_signers=trust.trusted_signers(),
        payload_root=extracted,
    )


def test_windows_product_builder_requires_and_embeds_winsw(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "python.exe").write_bytes(b"embedded-python")
    application = tmp_path / "application"
    package = application / "knoa_platform"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = '1.0.0'\n", encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "release-key.pem"
    private_key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    winsw = tmp_path / "WinSW.exe"
    winsw.write_bytes(b"signed-winsw")

    archive = build_product_release(
        role="all",
        target_os="windows",
        target_arch="x86_64",
        runtime_source=runtime,
        application_source=application,
        output_directory=tmp_path / "releases",
        signing_key_path=private_key_path,
        key_id="product-key",
        version="3.0.0",
        winsw_source=winsw,
    )

    extracted = tmp_path / "windows-extracted"
    extract_bundle(archive, extracted)
    assert (extracted / "service" / "WinSW.exe").read_bytes() == b"signed-winsw"
    assert (extracted / "install" / "Install-KnoaBundle.ps1").is_file()
