from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from knoa_platform.release.manifest import (
    ReleaseKind,
    TargetPlatform,
    TrustedReleaseSigner,
    apply_artifact_modes,
    load_signed_manifest,
    verify_release,
)
from knoa_platform.release.archive import extract_bundle

HealthCheck = Callable[[Path], None]


@dataclass(frozen=True)
class UpdateResult:
    release_id: str
    previous_release_id: str
    activated: bool
    rolled_back: bool


class ReleaseStore:
    """Version store with a small atomic pointer and health-based rollback."""

    def __init__(
        self,
        root: Path,
        *,
        trusted_signers: dict[str, TrustedReleaseSigner],
        expected_release_kind: ReleaseKind,
        expected_target: TargetPlatform,
        expected_role: str | None,
        health_check: HealthCheck,
        agent_runtime_spi_version: int = 1,
    ) -> None:
        self._root = root
        self._trusted_signers = trusted_signers
        self._expected_release_kind = expected_release_kind
        self._expected_target = expected_target
        self._expected_role = expected_role
        self._health_check = health_check
        self._agent_runtime_spi_version = agent_runtime_spi_version
        self._versions = root / "versions"
        self._state_path = root / "state.json"

    def install(self, bundle_root: Path) -> UpdateResult:
        signed = load_signed_manifest(bundle_root / "release-manifest.json")
        verify_release(
            signed,
            trusted_signers=self._trusted_signers,
            payload_root=bundle_root,
        )
        apply_artifact_modes(signed, bundle_root)
        manifest = signed.manifest
        if manifest.release_kind != self._expected_release_kind:
            raise ValueError("Release kind does not match this installation")
        if manifest.target != self._expected_target:
            raise ValueError("Release target does not match this installation")
        if manifest.role != self._expected_role:
            raise ValueError("Release role does not match this installation")
        if not (
            manifest.protocols.agent_runtime_spi_min
            <= self._agent_runtime_spi_version
            <= manifest.protocols.agent_runtime_spi_max
        ):
            raise ValueError("Agent Runtime SPI is not compatible with this installation")
        release_id = signed.manifest.release_id
        self._versions.mkdir(parents=True, exist_ok=True)
        destination = self._versions / release_id
        if destination.exists():
            existing = load_signed_manifest(destination / "release-manifest.json")
            if existing != signed:
                raise ValueError("Release ID already exists with different content")
        else:
            with tempfile.TemporaryDirectory(
                prefix=f".{release_id}.",
                dir=self._versions,
            ) as staging_name:
                staging = Path(staging_name)
                self._copy_bundle(bundle_root, staging)
                os.replace(staging, destination)

        previous = self.current_release_id()
        self._write_state(current=release_id, previous=previous, failed="")
        try:
            self._health_check(destination)
        except Exception:
            self._write_state(
                current=previous,
                previous="",
                failed=release_id,
            )
            raise
        return UpdateResult(
            release_id=release_id,
            previous_release_id=previous,
            activated=True,
            rolled_back=False,
        )

    def install_archive(self, archive_path: Path) -> UpdateResult:
        self._root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".incoming.",
            dir=self._root,
        ) as incoming_name:
            incoming = Path(incoming_name)
            extract_bundle(archive_path, incoming)
            return self.install(incoming)

    def rollback(self) -> UpdateResult:
        state = self._read_state()
        current = str(state.get("current", ""))
        previous = str(state.get("previous", ""))
        if not current or not previous:
            raise LookupError("No previous Knoa release is available")
        candidate = self._versions / previous
        if not candidate.is_dir():
            raise LookupError("Previous Knoa release is missing")
        self._health_check(candidate)
        self._write_state(current=previous, previous=current, failed="")
        return UpdateResult(
            release_id=previous,
            previous_release_id=current,
            activated=True,
            rolled_back=True,
        )

    def current_release_id(self) -> str:
        return str(self._read_state().get("current", ""))

    def current_path(self) -> Path | None:
        release_id = self.current_release_id()
        return self._versions / release_id if release_id else None

    def _read_state(self) -> dict[str, object]:
        if not self._state_path.is_file():
            return {"schema_version": 1, "current": "", "previous": "", "failed": ""}
        state = json.loads(self._state_path.read_text(encoding="utf-8"))
        if state.get("schema_version") != 1:
            raise ValueError("Unsupported release store state")
        return state

    def _write_state(self, *, current: str, previous: str, failed: str) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "schema_version": 1,
                "current": current,
                "previous": previous,
                "failed": failed,
            },
            sort_keys=True,
            indent=2,
        ) + "\n"
        temporary = self._state_path.with_name(f".{self._state_path.name}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self._state_path)

    @staticmethod
    def _copy_bundle(source: Path, destination: Path) -> None:
        for item in source.iterdir():
            if item.is_symlink():
                raise ValueError("Release bundle cannot contain symlinks")
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=False)
            elif item.is_file():
                shutil.copy2(item, target)
            else:
                raise ValueError("Release bundle contains a non-file entry")


__all__ = ["ReleaseStore", "UpdateResult"]
