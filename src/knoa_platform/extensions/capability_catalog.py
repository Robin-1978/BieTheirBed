"""Signed static Capability Catalog with explicit version selection."""
from __future__ import annotations

import base64
import json
import platform
import sqlite3
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from knoa_platform import __version__
from knoa_platform.extensions.capability_bundle import CapabilityInstallPlan, CapabilityInstaller, _numeric_version
from knoa_platform.extensions.package_store import PackageStore
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


SafeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")]
OFFICIAL_CATALOG_TRUST_ROOTS = {
    "knoa-release-2026": "65u2mVs9l5AUiChK5-itivKtKih9PepniYsxjAYIH3U",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatalogSignature(_Model):
    key_id: SafeId
    value: str = Field(min_length=80, max_length=128)


class CapabilityCatalogEntry(_Model):
    id: SafeId
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$", max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    platform: str = Field(pattern=r"^>=\d+\.\d+\.\d+$")
    operating_systems: tuple[Literal["linux", "windows", "darwin"], ...] = ()
    architectures: tuple[str, ...] = ()
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str = Field(min_length=1, max_length=4096)
    permission_summary: tuple[str, ...] = ()
    revoked: bool = False
    revocation_severity: Literal["none", "warning", "critical"] = "none"


class CapabilityCatalog(_Model):
    schema_version: Literal["knoa-capability-catalog-v1"] = Field(alias="schema", serialization_alias="schema")
    catalog_id: SafeId
    generated_at: float = Field(ge=0)
    entries: tuple[CapabilityCatalogEntry, ...]
    signature: CatalogSignature


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class CapabilityCatalogService:
    def __init__(
        self,
        catalog_path: str | Path,
        *,
        trust_roots: dict[str, str],
        source_root: str | Path,
        database: str | Path,
        packages: PackageStore,
        installer: CapabilityInstaller,
    ) -> None:
        self._catalog_path = Path(catalog_path).resolve()
        self._trust_roots = dict(trust_roots)
        self._source_root = Path(source_root).resolve()
        self._database = Path(database)
        self._packages = packages
        self._installer = installer
        initialize_wal(self._database)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS capability_catalog_selections(
                principal_id TEXT NOT NULL, capability_id TEXT NOT NULL,
                mode TEXT NOT NULL, version TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(principal_id, capability_id)
            )""")

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._database, foreign_keys=True)

    def load(self) -> CapabilityCatalog:
        raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        catalog = CapabilityCatalog.model_validate(raw)
        public = self._trust_roots.get(catalog.signature.key_id)
        if public is None:
            raise PermissionError("Capability Catalog trust root is unknown")
        transcript = {key: value for key, value in raw.items() if key != "signature"}
        try:
            Ed25519PublicKey.from_public_bytes(_decode(public)).verify(
                _decode(catalog.signature.value), _canonical(transcript)
            )
        except (InvalidSignature, ValueError) as exc:
            raise PermissionError("Capability Catalog signature rejected") from exc
        keys = [(item.id, item.version) for item in catalog.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("Capability Catalog contains duplicate versions")
        return catalog

    def list_entries(self, principal_id: str) -> tuple[dict, ...]:
        catalog = self.load()
        with self._connect() as db:
            rows = db.execute("SELECT capability_id, mode, version FROM capability_catalog_selections WHERE principal_id=?", (principal_id,)).fetchall()
        selection = {str(row["capability_id"]): {"mode": row["mode"], "version": row["version"]} for row in rows}
        return tuple({**entry.model_dump(mode="json"), "selection": selection.get(entry.id, {"mode": "latest_compatible", "version": ""})} for entry in catalog.entries)

    def select(self, principal_id: str, capability_id: str, *, mode: Literal["pinned", "latest_compatible", "explicit"], version: str = "") -> dict:
        entry = self.resolve(capability_id, mode=mode, version=version)
        selected_version = entry.version if mode in {"pinned", "explicit"} else ""
        import time
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO capability_catalog_selections VALUES(?,?,?,?,?)", (principal_id, capability_id, mode, selected_version, time.time()))
        return {"capability_id": capability_id, "mode": mode, "version": selected_version, "resolved_version": entry.version}

    def resolve(self, capability_id: str, *, mode: str = "latest_compatible", version: str = "") -> CapabilityCatalogEntry:
        candidates = [item for item in self.load().entries if item.id == capability_id]
        if mode in {"pinned", "explicit"}:
            candidates = [item for item in candidates if item.version == version]
        candidates = [item for item in candidates if self._compatible(item)]
        if not candidates:
            raise LookupError("No compatible Capability version was found")
        candidates.sort(key=lambda item: _numeric_version(item.version), reverse=True)
        entry = candidates[0]
        if entry.revoked:
            raise PermissionError("Revoked Capability versions cannot be installed")
        return entry

    @staticmethod
    def _compatible(entry: CapabilityCatalogEntry) -> bool:
        if _numeric_version(__version__) < _numeric_version(entry.platform.removeprefix(">=")):
            return False
        current_os = platform.system().lower()
        current_arch = platform.machine().lower()
        return (not entry.operating_systems or current_os in entry.operating_systems) and (
            not entry.architectures or current_arch in {item.lower() for item in entry.architectures}
        )

    def source_path(self, entry: CapabilityCatalogEntry) -> Path:
        prefix = "relative://"
        if not entry.source.startswith(prefix):
            raise ValueError("Only bundled relative Catalog sources are supported in v1")
        relative = Path(entry.source.removeprefix(prefix))
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("Capability Catalog source path is invalid")
        source = (self._source_root / relative).resolve()
        try:
            source.relative_to(self._source_root)
        except ValueError as exc:
            raise ValueError("Capability Catalog source escapes its root") from exc
        if not source.is_dir() or source.is_symlink():
            raise LookupError("Capability Catalog package is unavailable")
        return source

    async def prepare(
        self,
        principal_id: str,
        capability_id: str,
        *,
        mode: str = "latest_compatible",
        version: str = "",
    ) -> CapabilityInstallPlan:
        entry = self.resolve(capability_id, mode=mode, version=version)
        source = self.source_path(entry)
        frozen = self._packages.import_directory(
            "capability", source, source_type="signed_catalog",
            source_locator=f"{self.load().catalog_id}:{entry.id}:{entry.version}",
            imported_by=principal_id,
        )
        if frozen.content_digest != entry.package_digest:
            raise PermissionError("Capability package digest rejected")
        plan = await self._installer.prepare(principal_id, source)
        if plan.package_digest != entry.package_digest or plan.version != entry.version or plan.capability_id != entry.id:
            raise PermissionError("Capability package identity rejected")
        return plan


__all__ = ["CapabilityCatalog", "CapabilityCatalogEntry", "CapabilityCatalogService", "OFFICIAL_CATALOG_TRUST_ROOTS"]
