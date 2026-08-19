from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from knoa_platform import __version__
from knoa_platform.release import pack_bundle
from scripts.build_release_bundle import build_bundle, load_private_key
from scripts.materialize_release_payload import materialize_payload


def build_product_release(
    *,
    role: str,
    target_os: str,
    target_arch: str,
    runtime_source: Path,
    application_source: Path,
    output_directory: Path,
    signing_key_path: Path,
    key_id: str,
    version: str = __version__,
    winsw_source: Path | None = None,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    if role != "all":
        raise ValueError("Product Release must be the universal all-role Host Bundle")
    archive = output_directory / f"knoa-host-{version}-{target_os}-{target_arch}.zip"
    if archive.exists():
        raise FileExistsError(f"Release archive already exists: {archive}")
    release_id = f"knoa-host-{target_os}-{target_arch}-{version}"
    with tempfile.TemporaryDirectory(prefix="knoa-product-release-") as directory:
        root = Path(directory)
        payload = root / "payload"
        bundle = root / "bundle"
        materialize_payload(
            role=role,
            target_os=target_os,
            runtime_source=runtime_source,
            application_source=application_source,
            output=payload,
            winsw_source=winsw_source,
        )
        build_bundle(
            payload=payload,
            output=bundle,
            release_kind="product",
            role=role,
            target_os=target_os,
            target_arch=target_arch,
            version=version,
            release_id=release_id,
            signing_key=load_private_key(signing_key_path),
            key_id=key_id,
        )
        pack_bundle(bundle, archive)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("all",), default="all")
    parser.add_argument("--target-os", choices=("windows", "linux"), required=True)
    parser.add_argument("--target-arch", choices=("x86_64", "aarch64"), required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--winsw", type=Path)
    args = parser.parse_args()
    archive = build_product_release(
        role=args.role,
        target_os=args.target_os,
        target_arch=args.target_arch,
        runtime_source=args.runtime,
        application_source=args.application,
        output_directory=args.output_directory,
        signing_key_path=args.signing_key,
        key_id=args.key_id,
        version=args.version,
        winsw_source=args.winsw,
    )
    print(json.dumps({"archive": str(archive.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
