"""Signed Knoa product and Agent Runtime Extension releases."""

from knoa_platform.release.archive import extract_bundle, pack_bundle
from knoa_platform.release.manifest import (
    ArtifactKind,
    ProtocolCompatibility,
    ReleaseArtifact,
    ReleaseKind,
    ReleaseManifest,
    ReleaseSignature,
    ReleaseTrustKey,
    ReleaseTrustStore,
    RuntimeExtensionDescriptor,
    SignedReleaseManifest,
    TargetPlatform,
    TrustedReleaseSigner,
    apply_artifact_modes,
    load_signed_manifest,
    load_trust_store,
    sign_manifest,
    verify_release,
)
from knoa_platform.release.updater import ReleaseStore, UpdateResult

__all__ = [
    "ArtifactKind",
    "ProtocolCompatibility",
    "ReleaseArtifact",
    "ReleaseKind",
    "ReleaseManifest",
    "ReleaseSignature",
    "ReleaseStore",
    "ReleaseTrustKey",
    "ReleaseTrustStore",
    "RuntimeExtensionDescriptor",
    "SignedReleaseManifest",
    "TargetPlatform",
    "TrustedReleaseSigner",
    "UpdateResult",
    "apply_artifact_modes",
    "extract_bundle",
    "load_signed_manifest",
    "load_trust_store",
    "pack_bundle",
    "sign_manifest",
    "verify_release",
]
