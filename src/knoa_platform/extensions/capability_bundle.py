"""Generic Capability Bundle validation and Config-Revision installation.

The bundle is only installation intent.  PackageStore freezes bytes while the
managed Config Revision remains the sole activation authority.
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

from knoa_platform import __version__
from knoa_platform.configuration import (
    ConfigDraft,
    ConfigPublishResult,
    ConfigValidationResult,
    ManagedConfig,
    ManagedMCPConfig,
    ManagedMCPToolPolicyConfig,
    ManagedSkillConfig,
)
from knoa_platform.extensions.import_service import ExtensionImportService
from knoa_platform.extensions.mcp_package import load_mcp_package
from knoa_platform.extensions.package_store import PackageStore
from knoa_platform.extensions.skill import load_skill_package, skill_package_digest
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


SafeId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$", min_length=1, max_length=128),
]
_MANIFEST = "capability.yaml"
_MAX_MANIFEST_BYTES = 128 * 1024


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityCompatibility(_Model):
    platform: str = ">=0.2.0"
    operating_systems: tuple[Literal["linux", "windows", "darwin"], ...] = ()
    architectures: tuple[str, ...] = ()


class CapabilityComponents(_Model):
    mcp: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()

    @model_validator(mode="after")
    def nonempty(self):
        if not self.mcp and not self.skills:
            raise ValueError("Capability Bundle requires at least one component")
        return self


class CapabilityRequestedTool(_Model):
    name: SafeId
    effect: Literal[
        "read_only", "internal_write", "local_write",
        "external_side_effect", "desktop_control",
    ]
    capabilities: frozenset[Literal[
        "host_read", "host_write", "shell", "network", "desktop_observe",
        "desktop_control", "memory_read", "memory_write", "mcp", "task_management",
    ]] = frozenset()
    risk: Literal["low", "medium", "high"]


class CapabilitySetupInput(_Model):
    name: SafeId
    kind: Literal["secret"] = "secret"
    required: bool = True
    description: str = Field(default="", max_length=500)


class CapabilityHealthCheck(_Model):
    kind: Literal[
        "mcp_inventory", "required_command", "disk_budget", "fixed_tool_call",
    ]
    component: str = Field(default="", max_length=256)
    value: str = Field(default="", max_length=1024)
    max_bytes: int = Field(default=0, ge=0, le=10 * 1024 * 1024 * 1024)


class CapabilityEntryPoint(_Model):
    title: str = Field(min_length=1, max_length=120)
    mode: Literal["immediate", "background", "immediate_or_background"]


class CapabilityManifest(_Model):
    schema_version: Literal[1]
    id: SafeId
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$", max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    compatibility: CapabilityCompatibility = Field(default_factory=CapabilityCompatibility)
    components: CapabilityComponents
    requested_tools: tuple[CapabilityRequestedTool, ...] = ()
    setup_inputs: tuple[CapabilitySetupInput, ...] = ()
    health_checks: tuple[CapabilityHealthCheck, ...] = ()
    entry_points: tuple[CapabilityEntryPoint, ...] = ()

    @model_validator(mode="after")
    def unique_values(self):
        paths = (*self.components.mcp, *self.components.skills)
        if len(set(paths)) != len(paths):
            raise ValueError("Capability component paths must be unique")
        names = tuple(item.name for item in self.requested_tools)
        if len(set(names)) != len(names):
            raise ValueError("Capability requested Tool names must be unique")
        setup = tuple(item.name for item in self.setup_inputs)
        if len(set(setup)) != len(setup):
            raise ValueError("Capability setup input names must be unique")
        return self


class CapabilityInstallPlan(_Model):
    operation_id: SafeId
    capability_id: SafeId
    version: str
    display_name: str
    package_id: str
    package_digest: str
    component_packages: dict[str, str]
    requested_tools: tuple[CapabilityRequestedTool, ...]
    withheld_tools: tuple[str, ...]
    setup_inputs: tuple[CapabilitySetupInput, ...]
    checks: tuple[dict[str, Any], ...]
    draft_id: str
    draft_version: int
    previous_revision_id: str
    plan_digest: str
    state: Literal["awaiting_confirmation", "installing", "installed", "failed"]


class CapabilityInstallation(_Model):
    capability_id: SafeId
    version: str
    display_name: str
    package_id: str
    component_packages: dict[str, str]
    component_ids: tuple[str, ...]
    active_revision_id: str
    previous_revision_id: str
    enabled: bool
    health: Literal["healthy", "failed", "disabled"]
    installed_at: float
    updated_at: float


class CapabilityConfigPort(Protocol):
    async def get_config_current(self, principal_id: str): ...
    async def create_config_draft(self, principal_id: str) -> ConfigDraft: ...
    async def replace_config_draft(self, principal_id: str, draft_id: str, document: ManagedConfig, *, expected_version: int) -> ConfigDraft: ...
    async def validate_config_draft(self, principal_id: str, draft_id: str, *, preflight: bool = False) -> ConfigValidationResult: ...
    async def publish_config_draft(self, principal_id: str, draft_id: str, *, expected_version: int, summary: str = "") -> ConfigPublishResult: ...
    async def rollback_config(self, principal_id: str, revision_id: str, *, summary: str = "") -> ConfigPublishResult: ...


def _safe_component(root: Path, relative: str) -> Path:
    candidate = Path(relative or ".")
    if candidate.is_absolute():
        raise ValueError("Capability component paths must be relative")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Capability component escapes the bundle") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("Capability component must be a regular directory")
    return resolved


def load_capability_bundle(source: str | Path) -> tuple[CapabilityManifest, Path]:
    root = Path(source).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Capability Bundle source must be a directory")
    path = root / _MANIFEST
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Capability Bundle requires {_MANIFEST}")
    data = path.read_bytes()
    if len(data) > _MAX_MANIFEST_BYTES:
        raise ValueError("Capability manifest is too large")
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("Capability manifest is invalid YAML") from exc
    manifest = CapabilityManifest.model_validate(raw)
    for relative in (*manifest.components.mcp, *manifest.components.skills):
        _safe_component(root, relative)
    _validate_compatibility(manifest.compatibility)
    return manifest, root


def _numeric_version(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Capability platform compatibility is invalid")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _validate_compatibility(value: CapabilityCompatibility) -> None:
    constraint = value.platform.strip()
    if not constraint.startswith(">=") or _numeric_version(__version__) < _numeric_version(constraint[2:]):
        raise ValueError("Capability requires an incompatible Platform version")
    current_os = platform.system().lower()
    if value.operating_systems and current_os not in value.operating_systems:
        raise ValueError("Capability does not support this operating system")
    current_arch = platform.machine().lower()
    if value.architectures and current_arch not in {item.lower() for item in value.architectures}:
        raise ValueError("Capability does not support this architecture")


class CapabilityInstallationRepository:
    def __init__(self, database: str | Path, *, clock=time.time) -> None:
        self._path = Path(database).expanduser().resolve()
        self._clock = clock
        initialize_wal(self._path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS capability_operations(
                    operation_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capability_installations(
                    principal_id TEXT NOT NULL, capability_id TEXT NOT NULL,
                    installation_json TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(principal_id, capability_id)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    def put_plan(self, principal_id: str, plan: CapabilityInstallPlan) -> CapabilityInstallPlan:
        now = self._clock()
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO capability_operations VALUES (?, ?, ?, COALESCE((SELECT created_at FROM capability_operations WHERE operation_id=?), ?), ?)",
                (plan.operation_id, principal_id, plan.model_dump_json(), plan.operation_id, now, now),
            )
        return plan

    def plan(self, principal_id: str, operation_id: str) -> CapabilityInstallPlan:
        with self._connect() as db:
            row = db.execute(
                "SELECT plan_json FROM capability_operations WHERE principal_id=? AND operation_id=?",
                (principal_id, operation_id),
            ).fetchone()
        if row is None:
            raise LookupError("Capability installation operation not found")
        return CapabilityInstallPlan.model_validate_json(str(row["plan_json"]))

    def put_installation(self, principal_id: str, value: CapabilityInstallation) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO capability_installations VALUES (?, ?, ?, ?)",
                (principal_id, value.capability_id, value.model_dump_json(), self._clock()),
            )

    def installations(self, principal_id: str) -> tuple[CapabilityInstallation, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT installation_json FROM capability_installations WHERE principal_id=? ORDER BY capability_id",
                (principal_id,),
            ).fetchall()
        return tuple(CapabilityInstallation.model_validate_json(str(row[0])) for row in rows)

    def installation(self, principal_id: str, capability_id: str) -> CapabilityInstallation:
        values = [item for item in self.installations(principal_id) if item.capability_id == capability_id]
        if not values:
            raise LookupError("Capability installation not found")
        return values[0]


class CapabilityInstaller:
    def __init__(
        self,
        packages: PackageStore,
        configuration: CapabilityConfigPort,
        repository: CapabilityInstallationRepository,
        *,
        inspector: ExtensionImportService | None = None,
        clock=time.time,
    ) -> None:
        self._packages = packages
        self._configuration = configuration
        self._repository = repository
        self._inspector = inspector or ExtensionImportService(packages, configuration)
        self._clock = clock

    def list_installations(self, principal_id: str) -> tuple[CapabilityInstallation, ...]:
        return self._repository.installations(principal_id)

    async def prepare(self, principal_id: str, source: str | Path) -> CapabilityInstallPlan:
        manifest, root = load_capability_bundle(source)
        bundle_package = self._packages.import_directory(
            "capability", root, imported_by=principal_id,
        )
        frozen_manifest, frozen_root = load_capability_bundle(bundle_package.path)
        current, _state, _diff = await self._configuration.get_config_current(principal_id)
        draft = await self._configuration.create_config_draft(principal_id)
        document = draft.document
        servers = dict(document.mcp_servers)
        skills = dict(document.skills)
        component_packages: dict[str, str] = {}
        inventory_tools: set[str] = set()
        inventory_by_server: dict[str, set[str]] = {}
        component_ids: list[str] = []

        for relative in frozen_manifest.components.mcp:
            component = _safe_component(frozen_root, relative)
            connection = load_mcp_package(component)
            server_id = component.name
            package = self._packages.import_directory("mcp", component, imported_by=principal_id)
            frozen_connection = load_mcp_package(package.path)
            inspection = await self._inspector._inspect_mcp(server_id, frozen_connection, package.package_id)
            server_inventory = {str(tool["name"]) for tool in inspection.tools}
            inventory_tools.update(server_inventory)
            inventory_by_server[server_id] = server_inventory
            component_packages[f"mcp:{server_id}"] = package.package_id
            component_ids.append(f"mcp:{server_id}")
            servers[server_id] = ManagedMCPConfig(
                transport=connection.transport,
                package_id=package.package_id,
                inventory_digest=inspection.inventory_digest,
                enabled=True,
                inherit_env=connection.inherit_env,
                optional_env=connection.optional_env,
                timeout_seconds=connection.timeout_seconds,
                tools={},
            )
        for relative in frozen_manifest.components.skills:
            component = _safe_component(frozen_root, relative)
            loaded = load_skill_package(component)
            package = self._packages.import_directory("skill", component, imported_by=principal_id)
            frozen = load_skill_package(package.path)
            component_packages[f"skill:{loaded.manifest.id}"] = package.package_id
            component_ids.append(f"skill:{loaded.manifest.id}")
            skills[loaded.manifest.id] = ManagedSkillConfig(
                package_id=package.package_id,
                source=str(package.path),
                enabled=True,
                content_digest=skill_package_digest(frozen),
            )

        declared = {item.name for item in frozen_manifest.requested_tools}
        missing = declared - inventory_tools
        if missing:
            raise ValueError(f"Capability requests Tools absent from inventory: {', '.join(sorted(missing))}")
        policies = {
            item.name: ManagedMCPToolPolicyConfig(
                effect=item.effect, capabilities=item.capabilities, risk=item.risk,
            )
            for item in frozen_manifest.requested_tools
        }
        for server_id in [item.removeprefix("mcp:") for item in component_ids if item.startswith("mcp:")]:
            managed = servers[server_id]
            server_tools = {
                name: policy
                for name, policy in policies.items()
                if name in inventory_by_server[server_id]
            }
            servers[server_id] = managed.model_copy(update={"tools": server_tools})

        draft = await self._configuration.replace_config_draft(
            principal_id,
            draft.draft_id,
            document.model_copy(update={"mcp_servers": servers, "skills": skills}),
            expected_version=draft.draft_version,
        )
        checks = self._declarative_checks(frozen_manifest, frozen_root)
        operation_id = "capop-" + hashlib.sha256(
            f"{principal_id}:{bundle_package.content_digest}:{time.time_ns()}".encode()
        ).hexdigest()[:24]
        base = {
            "operation_id": operation_id,
            "capability_id": frozen_manifest.id,
            "version": frozen_manifest.version,
            "display_name": frozen_manifest.display_name,
            "package_id": bundle_package.package_id,
            "package_digest": bundle_package.content_digest,
            "component_packages": component_packages,
            "requested_tools": [item.model_dump(mode="json") for item in frozen_manifest.requested_tools],
            "withheld_tools": sorted(inventory_tools - declared),
            "setup_inputs": [item.model_dump(mode="json") for item in frozen_manifest.setup_inputs],
            "checks": list(checks),
            "draft_id": draft.draft_id,
            "draft_version": draft.draft_version,
            "previous_revision_id": current.revision_id,
        }
        plan_digest = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        plan = CapabilityInstallPlan.model_validate({
            **base, "plan_digest": plan_digest, "state": "awaiting_confirmation",
        })
        return self._repository.put_plan(principal_id, plan)

    def _declarative_checks(self, manifest: CapabilityManifest, root: Path) -> tuple[dict[str, Any], ...]:
        results: list[dict[str, Any]] = []
        for check in manifest.health_checks:
            if check.kind == "required_command":
                available = bool(check.value and shutil.which(check.value))
                results.append({"kind": check.kind, "value": check.value, "ready": available})
            elif check.kind == "disk_budget":
                free = shutil.disk_usage(root).free
                results.append({"kind": check.kind, "required_bytes": check.max_bytes, "available_bytes": free, "ready": free >= check.max_bytes})
            else:
                results.append({"kind": check.kind, "component": check.component, "ready": True})
        blocked = [item for item in results if not item["ready"]]
        if blocked:
            raise ValueError("Capability declarative preflight failed")
        return tuple(results)

    async def confirm(self, principal_id: str, operation_id: str, plan_digest: str) -> CapabilityInstallation:
        plan = self._repository.plan(principal_id, operation_id)
        if plan.state != "awaiting_confirmation" or plan.plan_digest != plan_digest:
            raise ValueError("Capability confirmation does not match the frozen plan")
        installing = plan.model_copy(update={"state": "installing"})
        self._repository.put_plan(principal_id, installing)
        validation = await self._configuration.validate_config_draft(
            principal_id, plan.draft_id, preflight=True,
        )
        if not validation.valid:
            self._repository.put_plan(principal_id, plan.model_copy(update={"state": "failed"}))
            raise ValueError("Capability Config preflight failed")
        result = await self._configuration.publish_config_draft(
            principal_id, plan.draft_id, expected_version=plan.draft_version,
            summary=f"Install Capability {plan.capability_id} {plan.version}",
        )
        if result.state.apply_status != "idle" or result.state.applied_revision_id != result.revision.revision_id:
            await self._configuration.rollback_config(
                principal_id, plan.previous_revision_id,
                summary=f"Rollback failed Capability {plan.capability_id} install",
            )
            self._repository.put_plan(principal_id, plan.model_copy(update={"state": "failed"}))
            raise RuntimeError("Capability health verification failed; previous Config restored")
        now = self._clock()
        value = CapabilityInstallation(
            capability_id=plan.capability_id, version=plan.version,
            display_name=plan.display_name, package_id=plan.package_id,
            component_packages=plan.component_packages,
            component_ids=tuple(sorted(plan.component_packages)),
            active_revision_id=result.revision.revision_id,
            previous_revision_id=plan.previous_revision_id,
            enabled=True, health="healthy", installed_at=now, updated_at=now,
        )
        self._repository.put_installation(principal_id, value)
        self._repository.put_plan(principal_id, plan.model_copy(update={"state": "installed"}))
        return value

    async def set_enabled(self, principal_id: str, capability_id: str, enabled: bool) -> CapabilityInstallation:
        installation = self._repository.installation(principal_id, capability_id)
        current, _state, _diff = await self._configuration.get_config_current(principal_id)
        draft = await self._configuration.create_config_draft(principal_id)
        servers = dict(draft.document.mcp_servers)
        skills = dict(draft.document.skills)
        for component_id in installation.component_ids:
            kind, item_id = component_id.split(":", 1)
            if kind == "mcp" and item_id in servers:
                servers[item_id] = servers[item_id].model_copy(update={"enabled": enabled})
            if kind == "skill" and item_id in skills:
                skills[item_id] = skills[item_id].model_copy(update={"enabled": enabled})
        draft = await self._configuration.replace_config_draft(
            principal_id, draft.draft_id,
            draft.document.model_copy(update={"mcp_servers": servers, "skills": skills}),
            expected_version=draft.draft_version,
        )
        result = await self._configuration.publish_config_draft(
            principal_id, draft.draft_id, expected_version=draft.draft_version,
            summary=f"{'Enable' if enabled else 'Disable'} Capability {capability_id}",
        )
        if result.state.apply_status != "idle":
            await self._configuration.rollback_config(principal_id, current.revision_id, summary="Restore Capability state")
            raise RuntimeError("Capability state change failed")
        updated = installation.model_copy(update={
            "active_revision_id": result.revision.revision_id,
            "previous_revision_id": current.revision_id,
            "enabled": enabled,
            "health": "healthy" if enabled else "disabled",
            "updated_at": self._clock(),
        })
        self._repository.put_installation(principal_id, updated)
        return updated

    async def rollback(self, principal_id: str, capability_id: str) -> CapabilityInstallation:
        installation = self._repository.installation(principal_id, capability_id)
        result = await self._configuration.rollback_config(
            principal_id, installation.previous_revision_id,
            summary=f"Rollback Capability {capability_id}",
        )
        if result.state.apply_status != "idle":
            raise RuntimeError("Capability rollback failed closed")
        updated = installation.model_copy(update={
            "active_revision_id": result.revision.revision_id,
            "enabled": False, "health": "disabled", "updated_at": self._clock(),
        })
        self._repository.put_installation(principal_id, updated)
        return updated


__all__ = [
    "CapabilityCompatibility", "CapabilityComponents", "CapabilityHealthCheck",
    "CapabilityInstallPlan", "CapabilityInstallation", "CapabilityInstallationRepository",
    "CapabilityInstaller", "CapabilityManifest", "CapabilityRequestedTool",
    "load_capability_bundle",
]
