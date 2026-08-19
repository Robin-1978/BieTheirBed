from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knoa_platform import __version__
from knoa_platform.release.manifest import (
    ReleaseArtifact,
    ReleaseManifest,
    RuntimeExtensionDescriptor,
    TargetPlatform,
    sign_manifest,
    utc_now,
)
from knoa_platform.release.archive import pack_bundle


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("Release signing key must be an Ed25519 private key")
    return key


def _kind(path: PurePosixPath, release_kind: str) -> str:
    top = path.parts[0]
    if release_kind == "runtime_extension":
        if top == "worker":
            return "runtime_extension_worker"
        return "metadata"
    return {
        "runtime": "python_runtime",
        "app": "application",
        "bin": "launcher",
        "console": "console_assets",
    }.get(top, "metadata")


def _artifacts(
    payload: Path,
    release_kind: str,
    target_os: str,
) -> tuple[ReleaseArtifact, ...]:
    artifacts: list[ReleaseArtifact] = []
    for candidate in sorted(payload.rglob("*")):
        if candidate.is_symlink():
            raise ValueError("Release payload cannot contain symlinks")
        if not candidate.is_file():
            continue
        relative = PurePosixPath(candidate.relative_to(payload).as_posix())
        digest = hashlib.sha256()
        size = 0
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        mode = candidate.stat().st_mode
        kind = _kind(relative, release_kind)
        executable = bool(mode & stat.S_IXUSR)
        if target_os == "windows" and candidate.suffix.lower() in {
            ".bat",
            ".cmd",
            ".exe",
            ".ps1",
        }:
            executable = True
        artifacts.append(
            ReleaseArtifact(
                path=relative.as_posix(),
                kind=kind,
                size=size,
                sha256=digest.hexdigest(),
                executable=executable,
            )
        )
    return tuple(artifacts)


def build_bundle(
    *,
    payload: Path,
    output: Path,
    release_kind: str,
    role: str | None,
    target_os: str,
    target_arch: str,
    version: str,
    release_id: str,
    signing_key: Ed25519PrivateKey,
    key_id: str,
    extension: RuntimeExtensionDescriptor | None = None,
) -> ReleaseManifest:
    if not payload.is_dir():
        raise ValueError("Release payload directory is missing")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for item in payload.iterdir():
        target = output / item.name
        if item.is_symlink():
            raise ValueError("Release payload cannot contain symlinks")
        if item.is_dir():
            shutil.copytree(item, target, symlinks=False)
        elif item.is_file():
            shutil.copy2(item, target)
        else:
            raise ValueError("Release payload contains a non-file entry")
    manifest = ReleaseManifest(
        release_id=release_id,
        version=version,
        release_kind=release_kind,
        role=role,
        target=TargetPlatform(os=target_os, arch=target_arch),
        created_at=utc_now(),
        artifacts=_artifacts(output, release_kind, target_os),
        extension=extension,
    )
    (output / "release-manifest.json").write_text(
        sign_manifest(manifest, signing_key, key_id=key_id).encoded(),
        encoding="utf-8",
    )
    return manifest


def _extension(args: argparse.Namespace) -> RuntimeExtensionDescriptor | None:
    if args.release_kind != "runtime_extension":
        return None
    descriptor = json.loads(args.extension_descriptor.read_text(encoding="utf-8"))
    return RuntimeExtensionDescriptor.model_validate(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--release-kind",
        choices=("product", "runtime_extension"),
        default="product",
    )
    parser.add_argument("--role", choices=("hub", "node", "all"))
    parser.add_argument("--target-os", choices=("windows", "linux"), required=True)
    parser.add_argument("--target-arch", choices=("x86_64", "aarch64"), required=True)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--release-id", default="")
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--extension-descriptor", type=Path)
    parser.add_argument(
        "--archive",
        type=Path,
        help="optionally create the deterministic cross-platform ZIP Bundle",
    )
    args = parser.parse_args()
    if args.release_kind == "product" and args.role is None:
        parser.error("product release requires --role")
    if args.release_kind == "runtime_extension" and (
        args.role is not None or args.extension_descriptor is None
    ):
        parser.error(
            "runtime_extension requires --extension-descriptor and forbids --role"
        )
    extension = _extension(args)
    release_id = args.release_id
    if not release_id:
        subject = args.role if extension is None else extension.extension_id
        release_id = (
            f"{subject}-{args.target_os}-{args.target_arch}-{args.version}"
        )
    manifest = build_bundle(
        payload=args.payload,
        output=args.output,
        release_kind=args.release_kind,
        role=args.role,
        target_os=args.target_os,
        target_arch=args.target_arch,
        version=args.version,
        release_id=release_id,
        signing_key=_load_private_key(args.signing_key),
        key_id=args.key_id,
        extension=extension,
    )
    if args.archive:
        pack_bundle(args.output, args.archive)
    print(
        json.dumps(
            {
                "release_id": manifest.release_id,
                "version": manifest.version,
                "target": manifest.target.model_dump(mode="json"),
                "artifacts": len(manifest.artifacts),
                "output": str(args.output.resolve()),
                "archive": str(args.archive.resolve()) if args.archive else "",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
