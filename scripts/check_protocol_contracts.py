from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ROOT = ROOT / "protocol"
PROTO = PROTOCOL_ROOT / "knoa" / "agent" / "runtime" / "v1" / "agent_runtime.proto"
DIGEST = PROTO.with_name("descriptor.sha256")
FIXTURES = PROTO.with_name("fixtures")
MESSAGE = "knoa.agent.runtime.v1.Envelope"
EXPECTED_PROTOC_VERSION = "31.1"


def _protoc() -> str:
    executable = shutil.which("protoc")
    if executable is None:
        raise RuntimeError("protoc is required to verify Knoa protocol contracts")
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != f"libprotoc {EXPECTED_PROTOC_VERSION}":
        raise RuntimeError(
            f"protoc {EXPECTED_PROTOC_VERSION} is required; found {version or 'unknown'}"
        )
    return executable


def _descriptor(protoc: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="knoa-protocol-") as directory:
        output = Path(directory) / "descriptor.pb"
        subprocess.run(
            [
                protoc,
                f"--proto_path={PROTOCOL_ROOT}",
                "--include_imports",
                f"--descriptor_set_out={output}",
                str(PROTO.relative_to(PROTOCOL_ROOT)),
            ],
            cwd=PROTOCOL_ROOT,
            check=True,
        )
        return output.read_bytes()


def _verify_fixtures(protoc: str) -> None:
    for fixture in sorted(FIXTURES.glob("*.textproto")):
        encoded = subprocess.run(
            [
                protoc,
                f"--proto_path={PROTOCOL_ROOT}",
                f"--encode={MESSAGE}",
                str(PROTO.relative_to(PROTOCOL_ROOT)),
            ],
            cwd=PROTOCOL_ROOT,
            input=fixture.read_bytes(),
            check=True,
            capture_output=True,
        ).stdout
        if not encoded:
            raise RuntimeError(f"Protocol fixture encoded to an empty frame: {fixture}")
        subprocess.run(
            [
                protoc,
                f"--proto_path={PROTOCOL_ROOT}",
                f"--decode={MESSAGE}",
                str(PROTO.relative_to(PROTOCOL_ROOT)),
            ],
            cwd=PROTOCOL_ROOT,
            input=encoded,
            check=True,
            capture_output=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    protoc = _protoc()
    digest = hashlib.sha256(_descriptor(protoc)).hexdigest()
    if args.update:
        DIGEST.write_text(f"{digest}\n", encoding="utf-8")
    elif not DIGEST.is_file() or DIGEST.read_text(encoding="utf-8").strip() != digest:
        raise RuntimeError(
            "Agent Runtime descriptor changed; review compatibility and run "
            "scripts/check_protocol_contracts.py --update"
        )
    _verify_fixtures(protoc)
    print(f"agent runtime protocol ok: sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
