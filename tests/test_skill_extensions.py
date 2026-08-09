from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pc_assistant.agent_runtime.composition import build_core_runtime
from pc_assistant.agent_runtime.contracts import HealthStatus, RunRequest
from pc_assistant.agent_runtime.model_step import ProviderChunk
from pc_assistant.config import AppConfig
from pc_assistant.extensions import ExtensionManager, ExtensionState
from pc_assistant.extensions.skill import (
    SkillCatalog,
    SkillPackageProvider,
    builtin_skill_root,
    load_skill_package,
)
from pc_assistant.tools.base import ToolCapability
from pc_assistant.tools.registry import ToolRegistry


def _write_skill(
    root: Path,
    skill_id: str,
    *,
    instructions: str = "Use <evidence> carefully.",
    resources: dict[str, str] | None = None,
    manifest_updates: dict | None = None,
) -> Path:
    package = root / skill_id
    package.mkdir(parents=True)
    (package / "instructions.md").write_text(instructions, encoding="utf-8")
    resource_names: list[str] = []
    for name, content in (resources or {}).items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        resource_names.append(name)
    manifest = {
        "id": skill_id,
        "version": "1.0.0",
        "name": skill_id,
        "description": f"{skill_id} description",
        "instructions": "instructions.md",
        "triggers": ["research report", "研究报告"],
        "resources": resource_names,
        "required_tools": ["web_search"],
        "required_capabilities": ["network"],
    }
    manifest.update(manifest_updates or {})
    (package / "skill.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return package


def test_skill_loader_confines_and_loads_text_resources(tmp_path: Path) -> None:
    package_root = _write_skill(
        tmp_path,
        "research",
        resources={"references/checklist.md": "verify every source"},
    )

    package = load_skill_package(package_root)

    assert package.manifest.id == "research"
    assert package.instructions == "Use <evidence> carefully."
    assert package.resources[0].path == "references/checklist.md"
    assert package.resources[0].content == "verify every source"


def test_skill_loader_rejects_escape_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    escaped = _write_skill(
        tmp_path,
        "escaped",
        manifest_updates={"instructions": "../outside.md"},
    )
    linked = _write_skill(tmp_path, "linked")
    (linked / "instructions.md").unlink()
    (linked / "instructions.md").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes"):
        load_skill_package(escaped)
    with pytest.raises(ValueError, match="escapes"):
        load_skill_package(linked)


def test_skill_catalog_activates_selectively_and_checks_dependencies(
    tmp_path: Path,
) -> None:
    package = load_skill_package(_write_skill(tmp_path, "research"))
    catalog = SkillCatalog()
    catalog.register(package)
    granted = frozenset({ToolCapability.NETWORK})

    inactive = catalog.active_context(
        "say hello",
        available_tools=frozenset({"web_search"}),
        capabilities=granted,
    )
    missing_tool = catalog.active_context(
        "create a research report",
        available_tools=frozenset(),
        capabilities=granted,
    )
    active = catalog.active_context(
        "create a research report",
        available_tools=frozenset({"web_search"}),
        capabilities=granted,
    )

    assert inactive == ""
    assert missing_tool == ""
    assert '<skill id="research"' in active
    assert "Use &lt;evidence&gt; carefully." in active


@pytest.mark.asyncio
async def test_invalid_skill_is_isolated_from_healthy_skill(tmp_path: Path) -> None:
    valid = _write_skill(tmp_path, "valid")
    invalid = _write_skill(tmp_path, "invalid")
    (invalid / "instructions.md").unlink()
    catalog = SkillCatalog()
    manager = ExtensionManager(
        ToolRegistry(),
        (
            SkillPackageProvider(invalid, catalog),
            SkillPackageProvider(valid, catalog),
        ),
    )

    await manager.start()

    assert [status.state for status in manager.statuses] == [
        ExtensionState.FAILED,
        ExtensionState.RUNNING,
    ]
    assert [package.manifest.id for package in catalog.packages] == ["valid"]

    await manager.stop()
    assert catalog.packages == ()


class _CaptureProvider:
    instance: _CaptureProvider | None = None

    def __init__(self, model) -> None:
        self.model_alias = model.alias
        self.requests = []
        type(self).instance = self

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)

    def stream(self, request, cancellation):
        del cancellation

        async def answer():
            self.requests.append(request)
            yield ProviderChunk(content_delta="done")
            yield ProviderChunk(finish_reason="stop", terminal=True)

        return answer()


@pytest.mark.asyncio
async def test_builtin_skill_reaches_model_only_for_matching_personal_run(
    tmp_path: Path,
) -> None:
    assert (builtin_skill_root() / "research_report" / "skill.yaml").is_file()
    composition = build_core_runtime(
        AppConfig(
            fallback_enabled=False,
            runtime_root=str(tmp_path / "runtime"),
            working_directory=str(tmp_path),
            service_port=0,
        ),
        provider_factory=_CaptureProvider,
    )
    await composition.extensions.start()
    try:
        scope = await composition.control.create_session("local")
        events = [
            event
            async for event in composition.application.run(
                "local",
                scope.session_handle,
                RunRequest(
                    client_request_id="request-a",
                    input="请深入研究这个主题并给我研究报告",
                ),
            )
        ]
        statuses = (await composition.control.get_status(scope)).extensions
    finally:
        await composition.extensions.stop()

    assert events[-1].event_type == "completed"
    provider = _CaptureProvider.instance
    assert provider is not None
    rendered = "\n".join(str(message["content"]) for message in provider.requests[0].messages)
    assert '<skill id="research_report"' in rendered
    assert "Search for multiple relevant sources" in rendered
    skill_status = next(status for status in statuses if status.kind == "skill")
    assert skill_status.extension_id == "skill:research_report"
    assert skill_status.state == "running"
