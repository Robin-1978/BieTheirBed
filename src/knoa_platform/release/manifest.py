from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ReleaseKind = Literal["product", "runtime_extension"]
ReleaseRole = Literal["hub", "node", "all"]
ArtifactKind = Literal[
    "python_runtime",
    "application",
    "launcher",
    "console_assets",
    "runtime_extension_worker",
    "metadata",
]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class _ReleaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TargetPlatform(_ReleaseModel):
    os: Literal["windows", "linux"]
    arch: Literal["x86_64", "aarch64"]


class ProtocolCompatibility(_ReleaseModel):
    release_manifest: Literal[1] = 1
    agent_runtime_spi_min: int = Field(default=1, ge=1)
    agent_runtime_spi_max: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> ProtocolCompatibility:
        if self.agent_runtime_spi_min > self.agent_runtime_spi_max:
            raise ValueError("Agent Runtime SPI range is reversed")
        return self


class ReleaseArtifact(_ReleaseModel):
    path: str = Field(min_length=1, max_length=1024)
    kind: ArtifactKind
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    executable: bool = False

    @model_validator(mode="after")
    def validate_path(self) -> ReleaseArtifact:
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or self.path != path.as_posix()
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in self.path
            or ":" in self.path
        ):
            raise ValueError("Release artifact path must be a safe POSIX relative path")
        return self


class RuntimeExtensionDescriptor(_ReleaseModel):
    extension_id: Identifier
    runtime_kind: Identifier
    display_name: str = Field(min_length=1, max_length=128)
    publisher: str = Field(min_length=1, max_length=256)
    entrypoint: tuple[str, ...] = Field(min_length=1, max_length=32)
    native_capability_ceiling: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_entrypoint(self) -> RuntimeExtensionDescriptor:
        if any(not part or len(part) > 1024 for part in self.entrypoint):
            raise ValueError("Runtime Extension entrypoint contains an invalid part")
        return self


class ReleaseManifest(_ReleaseModel):
    schema_version: Literal[1] = 1
    release_id: Identifier
    version: str = Field(min_length=5, max_length=128)
    release_kind: ReleaseKind
    role: ReleaseRole | None = None
    target: TargetPlatform
    created_at: datetime
    protocols: ProtocolCompatibility = Field(default_factory=ProtocolCompatibility)
    artifacts: tuple[ReleaseArtifact, ...] = Field(min_length=1, max_length=10_000)
    extension: RuntimeExtensionDescriptor | None = None

    @model_validator(mode="after")
    def validate_release(self) -> ReleaseManifest:
        if not _VERSION_PATTERN.fullmatch(self.version):
            raise ValueError("Release version must be SemVer-like")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Release created_at must include a timezone")
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("Release artifact paths must be unique")
        kinds = {artifact.kind for artifact in self.artifacts}
        if self.release_kind == "product":
            if self.role is None or self.extension is not None:
                raise ValueError("Product release requires role and forbids extension")
            required = {"application", "python_runtime", "launcher"}
            if not required <= kinds:
                raise ValueError(
                    "Product release requires application, python_runtime and launcher"
                )
            executable_kinds = {
                artifact.kind for artifact in self.artifacts if artifact.executable
            }
            if not {"python_runtime", "launcher"} <= executable_kinds:
                raise ValueError(
                    "Product release requires executable Runtime and launcher artifacts"
                )
        else:
            if self.role is not None or self.extension is None:
                raise ValueError(
                    "Runtime Extension release requires extension and forbids role"
                )
            if "runtime_extension_worker" not in kinds:
                raise ValueError("Runtime Extension release requires a worker artifact")
            if not any(
                artifact.kind == "runtime_extension_worker" and artifact.executable
                for artifact in self.artifacts
            ):
                raise ValueError("Runtime Extension worker must be executable")
        return self

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class ReleaseSignature(_ReleaseModel):
    algorithm: Literal["ed25519"] = "ed25519"
    key_id: Identifier
    value: str = Field(min_length=86, max_length=86)


class SignedReleaseManifest(_ReleaseModel):
    manifest: ReleaseManifest
    signature: ReleaseSignature

    def encoded(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"


class ReleaseTrustKey(_ReleaseModel):
    key_id: Identifier
    public_key: str = Field(min_length=43, max_length=43)
    allowed_release_kinds: frozenset[ReleaseKind] = Field(min_length=1)
    allowed_extension_ids: frozenset[Identifier] = frozenset()

    def signer(self) -> TrustedReleaseSigner:
        return TrustedReleaseSigner(
            public_key=Ed25519PublicKey.from_public_bytes(
                _base64url_decode(self.public_key)
            ),
            allowed_release_kinds=self.allowed_release_kinds,
            allowed_extension_ids=frozenset(self.allowed_extension_ids),
        )


class ReleaseTrustStore(_ReleaseModel):
    schema_version: Literal[1] = 1
    keys: tuple[ReleaseTrustKey, ...] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_keys(self) -> ReleaseTrustStore:
        key_ids = [key.key_id for key in self.keys]
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("Release trust key IDs must be unique")
        return self

    def trusted_signers(self) -> dict[str, TrustedReleaseSigner]:
        return {key.key_id: key.signer() for key in self.keys}


@dataclass(frozen=True)
class TrustedReleaseSigner:
    public_key: Ed25519PublicKey
    allowed_release_kinds: frozenset[ReleaseKind]
    allowed_extension_ids: frozenset[str] = frozenset()

    def authorize(self, manifest: ReleaseManifest) -> None:
        if manifest.release_kind not in self.allowed_release_kinds:
            raise PermissionError("Release signing key is not trusted for this kind")
        if manifest.release_kind == "runtime_extension":
            extension_id = manifest.extension.extension_id if manifest.extension else ""
            if (
                "*" not in self.allowed_extension_ids
                and extension_id not in self.allowed_extension_ids
            ):
                raise PermissionError(
                    "Release signing key is not trusted for this Runtime Extension"
                )


def sign_manifest(
    manifest: ReleaseManifest,
    private_key: Ed25519PrivateKey,
    *,
    key_id: str,
) -> SignedReleaseManifest:
    signature = private_key.sign(manifest.canonical_bytes())
    return SignedReleaseManifest(
        manifest=manifest,
        signature=ReleaseSignature(
            key_id=key_id,
            value=_base64url_encode(signature),
        ),
    )


def load_signed_manifest(path: Path) -> SignedReleaseManifest:
    return SignedReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_trust_store(path: Path) -> ReleaseTrustStore:
    return ReleaseTrustStore.model_validate_json(path.read_text(encoding="utf-8"))


def verify_release(
    signed: SignedReleaseManifest,
    *,
    trusted_signers: dict[str, TrustedReleaseSigner],
    payload_root: Path,
) -> None:
    signer = trusted_signers.get(signed.signature.key_id)
    if signer is None:
        raise PermissionError("Release signing key is not trusted")
    signer.authorize(signed.manifest)
    try:
        signer.public_key.verify(
            _base64url_decode(signed.signature.value),
            signed.manifest.canonical_bytes(),
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("Release manifest signature is invalid") from exc

    root = payload_root.resolve(strict=True)
    declared_paths = {artifact.path for artifact in signed.manifest.artifacts}
    actual_paths: set[str] = set()
    for candidate in payload_root.rglob("*"):
        relative = candidate.relative_to(payload_root).as_posix()
        if candidate.is_symlink():
            raise ValueError(f"Release bundle cannot contain symlinks: {relative}")
        if candidate.is_file() and relative != "release-manifest.json":
            actual_paths.add(relative)
    if actual_paths != declared_paths:
        missing = sorted(declared_paths - actual_paths)
        extra = sorted(actual_paths - declared_paths)
        raise ValueError(
            f"Release bundle file inventory mismatch: missing={missing}, extra={extra}"
        )
    for artifact in signed.manifest.artifacts:
        raw_candidate = root / Path(*PurePosixPath(artifact.path).parts)
        candidate = raw_candidate.resolve(strict=True)
        if candidate.parent != root and root not in candidate.parents:
            raise PermissionError("Release artifact escapes payload root")
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError(f"Release artifact is not a regular file: {artifact.path}")
        digest = hashlib.sha256()
        size = 0
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        if size != artifact.size or digest.hexdigest() != artifact.sha256:
            raise ValueError(f"Release artifact digest mismatch: {artifact.path}")


def apply_artifact_modes(signed: SignedReleaseManifest, payload_root: Path) -> None:
    if signed.manifest.target.os != "linux":
        return
    root = payload_root.resolve(strict=True)
    for artifact in signed.manifest.artifacts:
        candidate = (root / Path(*PurePosixPath(artifact.path).parts)).resolve(strict=True)
        candidate.chmod(0o700 if artifact.executable else 0o600)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ArtifactKind",
    "ProtocolCompatibility",
    "ReleaseArtifact",
    "ReleaseKind",
    "ReleaseManifest",
    "ReleaseSignature",
    "ReleaseTrustKey",
    "ReleaseTrustStore",
    "RuntimeExtensionDescriptor",
    "SignedReleaseManifest",
    "TargetPlatform",
    "TrustedReleaseSigner",
    "apply_artifact_modes",
    "load_signed_manifest",
    "load_trust_store",
    "sign_manifest",
    "utc_now",
    "verify_release",
]
