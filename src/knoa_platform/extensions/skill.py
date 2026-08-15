"""Safe data-only Skill packages and deterministic selective activation."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knoa_platform.context.tags import escape
from knoa_platform.extensions.manager import (
    ExtensionDescriptor,
    ExtensionKind,
    ExtensionProvider,
)
from knoa_platform.tools.base import ToolBase, ToolCapability


logger = logging.getLogger(__name__)

SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_INSTRUCTION_BYTES = 64 * 1024
_MAX_RESOURCE_BYTES = 128 * 1024
_MAX_TOTAL_RESOURCE_BYTES = 512 * 1024
_MAX_ACTIVE_SKILLS = 3
_MAX_ACTIVE_CONTEXT_CHARS = 12_000
_TEXT_RESOURCE_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml"})


def _escape_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    instructions: str
    triggers: tuple[str, ...] = Field(min_length=1, max_length=64)
    resources: tuple[str, ...] = Field(default=(), max_length=16)
    required_tools: frozenset[str] = frozenset()
    required_capabilities: frozenset[ToolCapability] = frozenset()

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = value.strip()
        if not SKILL_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Skill ID must contain 1-64 safe lowercase characters")
        return normalized

    @field_validator("instructions")
    @classmethod
    def validate_instruction_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Skill instructions path must not be empty")
        return normalized

    @field_validator("triggers")
    @classmethod
    def validate_triggers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().casefold() for value in values)
        if any(len(value) < 2 or len(value) > 80 for value in normalized):
            raise ValueError("Skill triggers must contain 2-80 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Skill triggers must be unique")
        return normalized

    @field_validator("required_tools")
    @classmethod
    def validate_tool_names(cls, values: frozenset[str]) -> frozenset[str]:
        if any(not value.strip() or len(value) > 256 for value in values):
            raise ValueError("Skill required tool names must contain 1-256 characters")
        return frozenset(value.strip() for value in values)

    @model_validator(mode="after")
    def validate_resource_paths(self) -> SkillManifest:
        paths = (self.instructions, *self.resources)
        if len(set(paths)) != len(paths):
            raise ValueError("Skill instruction and resource paths must be unique")
        return self


@dataclass(frozen=True)
class SkillResource:
    path: str
    content: str


@dataclass(frozen=True)
class SkillPackage:
    root: Path
    manifest: SkillManifest
    instructions: str
    resources: tuple[SkillResource, ...]


def _read_bounded(path: Path, limit: int, *, label: str) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{label} exceeds {limit} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8 text") from exc


def _package_file(root: Path, relative: str, *, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError(f"{label} must use a relative path")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the Skill package root") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist")
    return resolved


def load_skill_package(package_root: str | Path) -> SkillPackage:
    root = Path(package_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Skill package root must be a directory")
    manifest_path = root / "skill.yaml"
    manifest_text = _read_bounded(
        manifest_path,
        _MAX_MANIFEST_BYTES,
        label="Skill manifest",
    )
    raw = yaml.safe_load(manifest_text)
    if not isinstance(raw, dict):
        raise ValueError("Skill manifest must be a mapping")
    manifest = SkillManifest.model_validate(raw)
    if manifest.id != root.name:
        raise ValueError("Skill manifest ID must match its package directory")

    instruction_path = _package_file(
        root,
        manifest.instructions,
        label="Skill instructions",
    )
    if instruction_path.suffix.lower() not in {".md", ".txt"}:
        raise ValueError("Skill instructions must be Markdown or text")
    instructions = _read_bounded(
        instruction_path,
        _MAX_INSTRUCTION_BYTES,
        label="Skill instructions",
    ).strip()
    if not instructions:
        raise ValueError("Skill instructions must not be empty")

    resources: list[SkillResource] = []
    total_bytes = 0
    for relative in manifest.resources:
        resource_path = _package_file(root, relative, label="Skill resource")
        if resource_path.suffix.lower() not in _TEXT_RESOURCE_SUFFIXES:
            raise ValueError("Skill resources must use a supported text format")
        content = _read_bounded(
            resource_path,
            _MAX_RESOURCE_BYTES,
            label="Skill resource",
        )
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > _MAX_TOTAL_RESOURCE_BYTES:
            raise ValueError("Skill resources exceed the total size limit")
        resources.append(SkillResource(path=relative, content=content))
    return SkillPackage(
        root=root,
        manifest=manifest,
        instructions=instructions,
        resources=tuple(resources),
    )


class SkillCatalog:
    def __init__(self) -> None:
        self._packages: dict[str, SkillPackage] = {}

    @property
    def packages(self) -> tuple[SkillPackage, ...]:
        return tuple(self._packages[key] for key in sorted(self._packages))

    def register(self, package: SkillPackage) -> None:
        skill_id = package.manifest.id
        if skill_id in self._packages:
            raise ValueError(f"Skill is already registered: {skill_id}")
        self._packages[skill_id] = package

    def unregister(self, skill_id: str) -> None:
        self._packages.pop(skill_id, None)

    def active_context(
        self,
        query: str,
        *,
        available_tools: frozenset[str],
        capabilities: frozenset[ToolCapability],
        allowed_skills: frozenset[str],
    ) -> str:
        normalized = query.casefold()
        selected: list[tuple[int, SkillPackage]] = []
        for package in self.packages:
            manifest = package.manifest
            if manifest.id not in allowed_skills:
                continue
            hits = sum(1 for trigger in manifest.triggers if trigger in normalized)
            if not hits:
                continue
            if not manifest.required_tools <= available_tools:
                continue
            if not manifest.required_capabilities <= capabilities:
                continue
            selected.append((hits, package))
        selected.sort(key=lambda item: (-item[0], item[1].manifest.id))

        rendered: list[str] = []
        remaining = _MAX_ACTIVE_CONTEXT_CHARS
        for _hits, package in selected[:_MAX_ACTIVE_SKILLS]:
            block = self._render(package)
            if len(block) > remaining:
                if not rendered and remaining > 200:
                    rendered.append(block[: remaining - 20] + "\n[skill truncated]")
                break
            rendered.append(block)
            remaining -= len(block)
        if not rendered:
            return ""
        return "<active_skills>\n" + "\n".join(rendered) + "\n</active_skills>"

    @staticmethod
    def _render(package: SkillPackage) -> str:
        manifest = package.manifest
        parts = [
            f'<skill id="{_escape_attr(manifest.id)}" '
            f'version="{_escape_attr(manifest.version)}">',
            f"<instructions>\n{escape(package.instructions)}\n</instructions>",
        ]
        for resource in package.resources:
            parts.append(
                f'<resource path="{_escape_attr(resource.path)}">\n'
                f"{escape(resource.content)}\n</resource>"
            )
        parts.append("</skill>")
        return "\n".join(parts)


class SkillPackageProvider(ExtensionProvider):
    def __init__(self, package_root: str | Path, catalog: SkillCatalog) -> None:
        self._root = Path(package_root).expanduser().resolve()
        self._catalog = catalog
        self._skill_id = self._root.name
        if not SKILL_ID_PATTERN.fullmatch(self._skill_id):
            raise ValueError("Skill package directory must use a safe Skill ID")
        self._descriptor = ExtensionDescriptor(
            extension_id=f"skill:{self._skill_id}",
            kind=ExtensionKind.SKILL,
        )

    @property
    def descriptor(self) -> ExtensionDescriptor:
        return self._descriptor

    async def start(self) -> tuple[ToolBase, ...]:
        package = load_skill_package(self._root)
        self._catalog.register(package)
        return ()

    async def stop(self) -> None:
        self._catalog.unregister(self._skill_id)


def builtin_skill_root() -> Path:
    return Path(__file__).resolve().parent.parent / "skill_packages"


def build_skill_providers(
    roots: tuple[Path, ...],
    catalog: SkillCatalog,
) -> tuple[SkillPackageProvider, ...]:
    providers: list[SkillPackageProvider] = []
    for root in roots:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            continue
        for package_root in sorted(path for path in resolved.iterdir() if path.is_dir()):
            try:
                providers.append(SkillPackageProvider(package_root, catalog))
            except ValueError as exc:
                logger.warning("Ignoring invalid Skill directory %s: %s", package_root, exc)
    return tuple(providers)
