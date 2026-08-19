from __future__ import annotations

import argparse
import json
from pathlib import Path

from knoa_platform.release.manifest import ReleaseTrustStore, SignedReleaseManifest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "deploy" / "release" / "release-manifest-v1.schema.json"
TRUST_SCHEMA = ROOT / "deploy" / "release" / "release-trust-v1.schema.json"


def encoded_schema() -> str:
    return json.dumps(
        SignedReleaseManifest.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def encoded_trust_schema() -> str:
    return json.dumps(
        ReleaseTrustStore.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    encoded = encoded_schema()
    trust_encoded = encoded_trust_schema()
    if args.update:
        SCHEMA.write_text(encoded, encoding="utf-8")
        TRUST_SCHEMA.write_text(trust_encoded, encoding="utf-8")
    elif (
        not SCHEMA.is_file()
        or SCHEMA.read_text(encoding="utf-8") != encoded
        or not TRUST_SCHEMA.is_file()
        or TRUST_SCHEMA.read_text(encoding="utf-8") != trust_encoded
    ):
        raise RuntimeError(
            "Release Manifest schema changed; review compatibility and run "
            "scripts/check_release_manifest.py --update"
        )
    print("release manifest schema ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
