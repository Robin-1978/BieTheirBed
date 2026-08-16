"""Concrete composition root for the forward-only Core runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from knoa_agent import (
    ContextCheckpointRepository,
    DisabledToolSelector,
    KnoaAgentRuntime,
    ToolInventory,
)
from knoa_agent_contracts import (
    AgentRuntime,
    RuntimeTurnContext,
    TurnFinished,
    UsageReported,
)
from knoa_codex_agent import CodexAgentRuntime, CodexSessionRepository
from knoa_platform.agent_runtime.artifact_service import ArtifactService
from knoa_platform.agent_runtime.config_control import ConfigurationController
from knoa_platform.agent_runtime.contracts import (
    ExtensionStatusRecord,
    HealthStatus,
    RuntimeScope,
    ToolDescriptorRecord,
)
from knoa_platform.agent_runtime.control import ControlService
from knoa_platform.agent_runtime.http_provider import (
    FailoverModelProvider,
    HttpModelProvider,
)
from knoa_platform.agent_runtime.model_step import ModelProviderPort
from knoa_platform.agent_runtime.session_store import RuntimeSessionRepository
from knoa_platform.agent_runtime.tool_step import (
    ToolArgumentPolicy,
    ToolStep,
)
from knoa_platform.agent_runtime.transcription_service import (
    ArtifactTranscriptionService,
)
from knoa_platform.agents import (
    AgentDefinitionResolver,
    AgentExecutionService,
    AgentManager,
    AgentSessionBindingRepository,
    ExecuteAgentTurn,
    InvocationPolicyRepository,
)
from knoa_platform.agents.delegation import DelegationRepository, DelegationService
from knoa_platform.approvals import (
    APPROVAL_REVIEWER_SYSTEM_PROMPT,
    ApprovalReviewMode,
    KnoaReviewerAgent,
)
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.automation import (
    ScheduleDispatcher,
    ScheduleRepository,
    ScheduleService,
    TriggerDispatcher,
    TriggerRepository,
    TriggerService,
)
from knoa_platform.capabilities import (
    CapabilityGateway,
    CapabilityMCPHost,
    GatewayMCPConnector,
)
from knoa_platform.config import (
    AppConfig,
    ResolvedModelConfig,
    ThinkingConfig,
)
from knoa_platform.configuration import ConfigRegistry, ConfigurationService
from knoa_platform.configuration.models import (
    ConfigApplyError,
    ManagedConfig,
    ManagedSkillConfig,
)
from knoa_platform.context.memory_db import (
    ScopedEpisodicMemory,
    ScopedUserMemory,
    SQLiteMemoryRepository,
)
from knoa_platform.context.prompt import build_system_prompt
from knoa_platform.conversation import ConversationRepository, ConversationService
from knoa_platform.desktop_session import ensure_desktop_session
from knoa_platform.extensions import ExtensionManager
from knoa_platform.extensions.mcp import MCPServerProvider, build_mcp_providers
from knoa_platform.extensions.mcp_onboarding import MCPOnboardingService
from knoa_platform.extensions.mcp_package import (
    MCPPackageService,
    load_mcp_package,
)
from knoa_platform.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from knoa_platform.extensions.models import MCPServerConfig, MCPToolPolicyConfig
from knoa_platform.extensions.package_store import PackageStore
from knoa_platform.extensions.skill import (
    SkillCatalog,
    SkillPackageProvider,
    builtin_skill_root,
    load_skill_package,
    skill_package_digest,
)
from knoa_platform.interactions import (
    HumanInteractionRepository,
    HumanInteractionService,
)
from knoa_platform.observability.trace import LLMTraceRecorder, TurnRecorder
from knoa_platform.principal import converge_owner_principals, discover_owner_aliases
from knoa_platform.remote_models import RemoteModelProvider
from knoa_platform.runtime import RuntimePaths
from knoa_platform.secrets import SecretStore
from knoa_platform.service.core_auth import (
    CompositeAuthenticator,
    SignedPrincipalAuthenticator,
    StaticTokenAuthenticator,
)
from knoa_platform.service.core_host import (
    CoreServiceHost,
    TcpCoreEndpoint,
)
from knoa_platform.service.core_server import CoreServer
from knoa_platform.service.credentials import resolve_local_service_token
from knoa_platform.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskEventPayload,
    TaskExecutor,
    TaskRepository,
    TaskService,
)
from knoa_platform.tools.artifact_prepare import ArtifactPrepareTool
from knoa_platform.tools.base import ToolCapability, ToolEffect, ToolRisk
from knoa_platform.tools.clipboard import ClipboardTool
from knoa_platform.tools.create_task import CreateTaskTool
from knoa_platform.tools.describe_tool import DescribeTool
from knoa_platform.tools.exchange import ExchangeTool
from knoa_platform.tools.hotkey import HotkeyTool
from knoa_platform.tools.mcp_connect import (
    MCPConnectTool,
    MCPDisableTool,
    MCPInspectTool,
)
from knoa_platform.tools.memory_tool import MemoryTool
from knoa_platform.tools.mouse import MouseTool
from knoa_platform.tools.notification import NotificationTool
from knoa_platform.tools.press_key import PressKeyTool
from knoa_platform.tools.read_artifact import ReadArtifactTool
from knoa_platform.tools.read_file import ReadFileTool
from knoa_platform.tools.registry import ToolRegistry
from knoa_platform.tools.screenshot import ScreenshotTool
from knoa_platform.tools.shell import ShellTool
from knoa_platform.tools.subagent import SpawnSubagentTool, SubagentTool
from knoa_platform.tools.task_control import TaskControlTool
from knoa_platform.tools.type_text import TypeTextTool
from knoa_platform.tools.weather import WeatherTool
from knoa_platform.tools.web_fetch import WebFetchTool
from knoa_platform.tools.web_search import WebSearchTool
from knoa_platform.tools.window import WindowTool
from knoa_platform.tools.write_file import WriteFileTool

PERSONAL_LOCAL_CAPABILITIES = frozenset(ToolCapability)
REMOTE_SCOPED_CAPABILITIES = frozenset({ToolCapability.NETWORK})

CODER_PROFILE_PROMPT = """You are Knoa's focused coding specialist.
Work only on the explicitly delegated objective. Inspect the workspace before
editing, make cohesive changes, verify them, and report concrete results.
Treat Platform context and Skill instructions as supporting context, not as
permission. Use only the Runtime-native actions and Platform tools made
available for this invocation. Never create another agent or widen scope.
"""


class RuntimeModelProvider(ModelProviderPort, Protocol):
    """Model Provider capabilities required by runtime composition."""

    @property
    def model_alias(self) -> str: ...

    async def health_check(self) -> HealthStatus: ...


@dataclass(frozen=True)
class CoreRuntimeComposition:
    """Owned runtime graph and its independently managed service host."""

    paths: RuntimePaths
    sessions: RuntimeSessionRepository
    tasks: TaskRepository
    task_service: TaskService
    conversations: ConversationRepository
    conversation_service: ConversationService
    interactions: HumanInteractionService
    schedules: ScheduleRepository
    schedule_dispatcher: ScheduleDispatcher
    schedule_service: ScheduleService
    triggers: TriggerRepository
    trigger_dispatcher: TriggerDispatcher
    trigger_service: TriggerService
    memory: SQLiteMemoryRepository
    artifacts: ArtifactStore
    registry: ToolRegistry
    agent_manager: AgentManager
    agent_execution: AgentExecutionService
    agent_bindings: AgentSessionBindingRepository
    invocation_policies: InvocationPolicyRepository
    delegation_repository: DelegationRepository
    delegations: DelegationService
    capability_gateway: CapabilityGateway
    capability_mcp_host: CapabilityMCPHost
    control: ControlService
    artifact_service: ArtifactService
    transcription_service: ArtifactTranscriptionService
    llm_traces: LLMTraceRecorder
    turn_traces: TurnRecorder
    extensions: ExtensionManager
    mcp_resource_tasks: MCPResourceTaskBridge
    mcp_packages: MCPPackageService
    skills: SkillCatalog
    config_registry: ConfigRegistry
    configuration: ConfigurationService
    host: CoreServiceHost


def _usage_integer(usage: dict, *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, (int, float)):
            return max(0, int(value))
    return 0


def _cached_usage_tokens(usage: dict) -> int:
    direct = _usage_integer(
        usage,
        "cached_tokens",
        "cache_read_input_tokens",
    )
    if direct:
        return direct
    for details_name in ("prompt_tokens_details", "input_tokens_details"):
        details = usage.get(details_name)
        if isinstance(details, dict):
            nested = _usage_integer(
                details,
                "cached_tokens",
                "cache_read_input_tokens",
            )
            if nested:
                return nested
    return 0


def capabilities_for_scope(scope: RuntimeScope) -> frozenset[ToolCapability]:
    if scope.principal_id == "local" or scope.principal_id.startswith("personal:"):
        return PERSONAL_LOCAL_CAPABILITIES
    return REMOTE_SCOPED_CAPABILITIES


def _profile_instructions(profile) -> str:
    if profile.instructions:
        return profile.instructions
    builtins = {
        "builtin://assistant": build_system_prompt(),
        "builtin://approval-reviewer": APPROVAL_REVIEWER_SYSTEM_PROMPT,
        "builtin://coder": CODER_PROFILE_PROMPT,
    }
    try:
        return builtins[profile.instructions_ref]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported Profile instructions reference: {profile.instructions_ref}"
        ) from exc


def _managed_model_alias(config: ManagedConfig, agent_id: str) -> str:
    definition = config.agent_system.agents[agent_id]
    runtime = config.agent_system.runtime_specs[definition.runtime_spec_id]
    binding = runtime.model_binding
    if binding.ownership != "platform":
        raise ValueError(f"Agent '{agent_id}' does not use a Platform model")
    return binding.model


def _agent_generation_id(managed: ManagedConfig, agent_id: str) -> str:
    definition = managed.agent_system.agents[agent_id]
    runtime = managed.agent_system.runtime_specs[definition.runtime_spec_id]
    profile = managed.agent_system.profiles[definition.profile_id]
    runtime_material = runtime.model_dump(
        mode="json",
        exclude={"native_capabilities", "instruction_authority"},
    )
    payload: dict[str, object] = {
        "agent_id": agent_id,
        "runtime_spec_id": definition.runtime_spec_id,
        "runtime": runtime_material,
        "profile": {
            "display_name": profile.display_name,
            "instructions": profile.instructions,
            "instructions_ref": profile.instructions_ref,
            "runtime_limits": profile.runtime_limits.model_dump(mode="json"),
            "tool_inventory_enabled": bool(profile.allowed_platform_tools),
            "visibility": profile.visibility,
        },
    }
    if runtime.implementation == "native":
        model_alias = _managed_model_alias(managed, agent_id)
        model = managed.models[model_alias]
        payload.update(
            {
                "model_alias": model_alias,
                "model": model.model_dump(mode="json"),
                "provider": managed.providers[model.provider].model_dump(mode="json"),
                "operational": {
                    "llm_temperature": managed.operational.llm_temperature,
                    "max_iterations": managed.operational.max_iterations,
                    "max_total_tool_calls": managed.operational.max_total_tool_calls,
                    "max_output_tokens": managed.operational.max_output_tokens,
                    "context_window_budget": managed.operational.context_window_budget,
                },
            }
        )
        if agent_id == managed.agent_system.default_agent:
            payload["fallback"] = {
                "enabled": managed.fallback_enabled,
                "model": managed.fallback_model,
                "config": (
                    managed.models[managed.fallback_model].model_dump(mode="json")
                    if managed.fallback_enabled and managed.fallback_model
                    else None
                ),
            }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _extension_generation_id(managed: ManagedConfig) -> str:
    payload = {
        "skills": {
            skill_id: skill.model_dump(mode="json")
            for skill_id, skill in sorted(managed.skills.items())
        },
        "mcp_servers": {
            server_id: server.model_dump(mode="json")
            for server_id, server in sorted(managed.mcp_servers.items())
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _resolve_managed_model(
    managed: ManagedConfig,
    alias: str,
    bootstrap: AppConfig,
) -> ResolvedModelConfig:
    try:
        model = managed.models[alias]
        provider = managed.providers[model.provider]
    except KeyError as exc:
        raise ValueError(f"Unknown managed model '{alias}'") from exc
    api_key = ""
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env, "")
        if not api_key:
            raise ValueError(
                f"API key environment variable '{provider.api_key_env}' is not set"
            )
    elif model.provider == "bootstrap_provider":
        api_key = bootstrap.llm_api_key
    elif provider.api_key_ref:
        try:
            api_key = SecretStore(
                RuntimePaths.from_root(bootstrap.runtime_root).secrets / "providers"
            ).get(provider.api_key_ref)
        except LookupError as exc:
            raise ValueError(
                f"Provider secret '{provider.api_key_ref}' is not configured"
            ) from exc
    elif model.provider in bootstrap.providers:
        api_key = bootstrap.providers[model.provider].api_key.get_secret_value()
    required = (
        provider.requires_api_key
        if provider.requires_api_key is not None
        else provider.driver in {"openai", "openai_compatible", "anthropic"}
    )
    if required and not api_key:
        raise ValueError(f"Provider '{model.provider}' requires a configured secret")
    return ResolvedModelConfig(
        alias=alias,
        provider_name=model.provider,
        driver=provider.driver,
        server_url=provider.server_url or provider.api_base,
        api_base=provider.api_base,
        api_key=api_key,
        model=model.model,
        supports_vision=model.supports_vision,
        context_window=model.context_window,
        timeout=provider.timeout_seconds,
        thinking=(
            None if model.thinking is None else ThinkingConfig(type=model.thinking)
        ),
        remote_deployment_id=provider.remote_deployment_id,
        direct_gateway_url=provider.direct_gateway_url,
    )


def _build_agent_runtime_set(
    managed: ManagedConfig,
    *,
    bootstrap: AppConfig,
    paths: RuntimePaths,
    capability_gateway: CapabilityGateway,
    provider_factory: Callable[[ResolvedModelConfig], RuntimeModelProvider],
) -> tuple[dict[str, AgentRuntime], RuntimeModelProvider, ResolvedModelConfig, str]:
    runtimes: dict[str, AgentRuntime] = {}
    default_primary: RuntimeModelProvider | None = None
    default_model_config = _resolve_managed_model(
        managed,
        managed.default_model,
        bootstrap,
    )
    reviewer_model_alias = ""
    for agent_id, definition in managed.agent_system.agents.items():
        if not definition.enabled:
            continue
        runtime_spec = managed.agent_system.runtime_specs[
            definition.runtime_spec_id
        ]
        profile = managed.agent_system.profiles[definition.profile_id]
        instructions = _profile_instructions(profile)
        state_root = paths.resolve(
            f"agents/{agent_id}",
            default_parent=paths.root,
        )
        if runtime_spec.implementation == "native":
            model_alias = _managed_model_alias(managed, agent_id)
            model_config = _resolve_managed_model(managed, model_alias, bootstrap)
            selected_primary = provider_factory(model_config)
            runtime_provider: ModelProviderPort = selected_primary
            selected_fallback: RuntimeModelProvider | None = None
            if (
                agent_id == managed.agent_system.default_agent
                and managed.fallback_enabled
                and managed.fallback_model
            ):
                selected_fallback = provider_factory(
                    _resolve_managed_model(
                        managed,
                        managed.fallback_model,
                        bootstrap,
                    )
                )
                runtime_provider = FailoverModelProvider(
                    selected_primary,
                    selected_fallback,
                )

            async def native_health_probe(
                primary_provider=selected_primary,
                fallback_provider=selected_fallback,
            ) -> HealthStatus:
                primary_health = await primary_provider.health_check()
                if primary_health.healthy or fallback_provider is None:
                    return primary_health
                fallback_health = await fallback_provider.health_check()
                if fallback_health.healthy:
                    return HealthStatus(
                        healthy=True,
                        detail=(
                            "Fallback model available: "
                            f"{fallback_provider.model_alias}"
                        ),
                    )
                return HealthStatus(
                    healthy=False,
                    detail="No configured model is available",
                )

            profile_iterations = profile.runtime_limits.max_iterations
            profile_output_tokens = profile.runtime_limits.max_output_tokens
            runtimes[agent_id] = KnoaAgentRuntime(
                runtime_provider,
                ContextCheckpointRepository(state_root / "context.db"),
                GatewayMCPConnector(capability_gateway),
                system_prompt=instructions,
                health_probe=native_health_probe,
                max_iterations=(
                    profile_iterations or managed.operational.max_iterations
                ),
                max_tool_calls=max(1, managed.operational.max_total_tool_calls),
                max_output_tokens=(
                    profile_output_tokens or managed.operational.max_output_tokens
                ),
                temperature=(
                    0.0
                    if profile.visibility == "system"
                    else managed.operational.llm_temperature
                ),
                context_window=max(
                    512,
                    model_config.context_window
                    or managed.operational.context_window_budget,
                ),
                agent_id=agent_id,
                display_name=profile.display_name,
                tool_inventory=(
                    ToolInventory(semantic_selector=DisabledToolSelector())
                    if not profile.allowed_platform_tools
                    else None
                ),
            )
            if agent_id == managed.agent_system.default_agent:
                default_primary = selected_primary
                default_model_config = model_config
            if agent_id == managed.approval_review.agent_id:
                reviewer_model_alias = model_alias
            continue

        workspace = (
            paths.resolve(runtime_spec.cwd, default_parent=paths.root)
            if runtime_spec.cwd
            else state_root / "workspace"
        )
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime_home = (
            paths.resolve(runtime_spec.home, default_parent=paths.root)
            if runtime_spec.home
            else None
        )
        runtimes[agent_id] = CodexAgentRuntime(
            CodexSessionRepository(state_root / "sessions.db"),
            agent_id=agent_id,
            display_name=profile.display_name,
            instructions=instructions,
            command=runtime_spec.command,
            home=runtime_home,
            cwd=workspace,
            model=runtime_spec.model_binding.hint,
            approval_policy=runtime_spec.approval_policy,
            sandbox=runtime_spec.sandbox,
            request_timeout_seconds=runtime_spec.request_timeout_seconds,
            max_line_bytes=runtime_spec.max_line_bytes,
            max_event_queue=runtime_spec.max_event_queue,
        )
    if default_primary is None:
        raise ValueError("Default Agent must use a Platform-managed model")
    return runtimes, default_primary, default_model_config, reviewer_model_alias


def _build_registry(
    config: AppConfig,
    artifacts: ArtifactStore,
    memory: ScopedUserMemory,
    episodic: ScopedEpisodicMemory,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(working_directory=config.working_directory),
        ReadArtifactTool(artifacts),
        WriteFileTool(working_directory=config.working_directory),
        ShellTool(default_timeout=config.shell_timeout),
        WebSearchTool(),
        WebFetchTool(),
        ClipboardTool(),
        MemoryTool(memory=memory, episodic=episodic),
        WeatherTool(),
        ExchangeTool(),
        WindowTool(),
        NotificationTool(),
        PressKeyTool(),
        TypeTextTool(),
        HotkeyTool(),
        MouseTool(),
        ScreenshotTool(artifacts, artifacts.root / "screenshots"),
        ArtifactPrepareTool(
            artifacts,
            working_directory=config.working_directory,
        ),
    ):
        registry.register(tool)
    registry.register(DescribeTool(registry))
    return registry


def _bootstrap_managed_skills(
    roots: tuple[Path, ...],
) -> dict[str, ManagedSkillConfig]:
    skills: dict[str, ManagedSkillConfig] = {}
    for root in roots:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            continue
        for package_root in sorted(path for path in resolved.iterdir() if path.is_dir()):
            try:
                provider = SkillPackageProvider(package_root, SkillCatalog())
            except ValueError:
                continue
            skill_id = provider.descriptor.extension_id.removeprefix("skill:")
            skills.setdefault(
                skill_id,
                ManagedSkillConfig(
                    source=str(package_root),
                    enabled=True,
                    content_digest=skill_package_digest(
                        load_skill_package(package_root)
                    ),
                ),
            )
    return skills


def _freeze_skill_digests(
    managed: ManagedConfig,
    packages: PackageStore,
) -> ManagedConfig:
    frozen: dict[str, ManagedSkillConfig] = {}
    for skill_id, skill in sorted(managed.skills.items()):
        source = (
            packages.get(skill.package_id, expected_kind="skill").path
            if skill.package_id
            else Path(skill.source).expanduser().resolve()
        )
        if source.name != skill_id:
            raise ValueError(f"Skill source directory must match ID: {skill_id}")
        package = load_skill_package(source)
        frozen[skill_id] = skill.model_copy(
            update={"source": str(source), "content_digest": skill_package_digest(package)}
        )
    return managed.model_copy(update={"skills": frozen})


def _managed_skill_providers(
    managed: ManagedConfig,
    catalog: SkillCatalog,
    packages: PackageStore,
) -> tuple[SkillPackageProvider, ...]:
    return tuple(
        SkillPackageProvider(
            (
                packages.get(skill.package_id, expected_kind="skill").path
                if skill.package_id
                else skill.source
            ),
            catalog,
            expected_digest=skill.content_digest,
        )
        for skill_id, skill in sorted(managed.skills.items())
        if skill.enabled
        and (
            bool(skill.package_id)
            or Path(skill.source).expanduser().resolve().name == skill_id
        )
    )


def _managed_mcp_configs(
    managed: ManagedConfig,
    packages: PackageStore,
) -> dict[str, MCPServerConfig]:
    configs: dict[str, MCPServerConfig] = {}
    for server_id, server in managed.mcp_servers.items():
        policies = {
            name: MCPToolPolicyConfig(
                effect=ToolEffect(policy.effect),
                capabilities=frozenset(
                    ToolCapability(item) for item in policy.capabilities
                ),
                risk=ToolRisk(policy.risk),
            )
            for name, policy in server.tools.items()
        }
        if server.package_id:
            record = packages.get(server.package_id, expected_kind="mcp")
            package_config = load_mcp_package(record.path)
            configs[server_id] = package_config.model_copy(
                update={
                    "enabled": server.enabled,
                    "timeout_seconds": server.timeout_seconds,
                    "tools": policies,
                }
            )
            continue
        command = server.command[0] if server.command else ""
        args = server.command[1:]
        configs[server_id] = MCPServerConfig(
            enabled=server.enabled,
            transport=server.transport,
            url=server.url,
            command=command if command else "",
            args=tuple(args),
            working_directory=server.working_directory,
            inherit_env=server.inherit_env,
            optional_env=server.optional_env,
            timeout_seconds=server.timeout_seconds,
            tools=policies,
        )
    return configs


def _managed_mcp_providers(
    managed: ManagedConfig,
    *,
    secret_root: Path,
    packages: PackageStore,
) -> tuple[MCPServerProvider, ...]:
    return build_mcp_providers(
        _managed_mcp_configs(managed, packages),
        secret_root=secret_root,
        inventory_digests={
            server_id: server.inventory_digest
            for server_id, server in managed.mcp_servers.items()
            if server.inventory_digest
        },
    )


def _bootstrap_provider_secrets(config: AppConfig, store: SecretStore) -> None:
    """Import trusted file-config credentials once into the forward SecretStore."""

    for provider_id, provider in config.providers.items():
        value = provider.api_key.get_secret_value()
        if not value:
            continue
        reference = f"provider.{provider_id}.api_key"
        if not store.status(reference)["configured"]:
            store.put(reference, value)


def build_core_runtime(
    config: AppConfig,
    *,
    provider_factory: Callable[[ResolvedModelConfig], RuntimeModelProvider] | None = None,
) -> CoreRuntimeComposition:
    """Build one Core graph without legacy agents or in-process service fallback."""

    paths = RuntimePaths.from_root(config.runtime_root)
    if provider_factory is None:
        def provider_factory(model: ResolvedModelConfig) -> RuntimeModelProvider:
            if model.driver == "workspace_remote":
                return RemoteModelProvider(model, paths=paths)
            return HttpModelProvider(model)
    packages = PackageStore(paths.packages)
    provider_secrets = SecretStore(paths.secrets / "providers")
    _bootstrap_provider_secrets(config, provider_secrets)
    skill_roots = (
        builtin_skill_root(),
        paths.skills,
        *(paths.resolve(directory) for directory in config.skill_directories),
    )
    converge_owner_principals(
        paths,
        config.owner_principal_id,
        discover_owner_aliases(paths, config.owner_principal_aliases),
    )
    llm_traces = LLMTraceRecorder(
        str(paths.resolve(config.llm_trace_log)),
        enabled=config.trace_enabled,
    )
    turn_traces = TurnRecorder(
        str(paths.resolve(config.turn_trace_log)),
        enabled=config.trace_enabled,
    )
    database = paths.data / "assistant.db"
    config_registry = ConfigRegistry(database)
    bootstrap_managed = config.managed_config()
    bootstrap_managed = bootstrap_managed.model_copy(
        update={"skills": _bootstrap_managed_skills(skill_roots)}
    )
    bootstrap_managed = _freeze_skill_digests(bootstrap_managed, packages)
    applied_config = config_registry.initialize(
        bootstrap_managed,
        actor=config.owner_principal_id,
    )
    frozen_applied = _freeze_skill_digests(applied_config.document, packages)
    applied_config = config_registry.adopt(
        frozen_applied,
        actor=config.owner_principal_id,
        summary="Freeze managed Skill package content",
    )
    managed = applied_config.document
    agent_resolver = AgentDefinitionResolver(
        managed.agent_system,
        config_revision_id=applied_config.revision_id,
    )
    resolver_holder = {"current": agent_resolver}
    managed_holder = {"current": managed}
    model_holder: dict[str, ResolvedModelConfig] = {}
    sessions = RuntimeSessionRepository(database)
    prompt_budget = max(
        256,
        managed.operational.context_window_budget
        - managed.operational.max_output_tokens,
    )
    tasks = TaskRepository(
        database,
        trace_retention_seconds=config.task_trace_retention_days * 24 * 60 * 60,
    )
    schedules = ScheduleRepository(database)
    triggers = TriggerRepository(database)
    memory_repository = SQLiteMemoryRepository(database)
    memory = ScopedUserMemory(memory_repository)
    episodic = ScopedEpisodicMemory(memory_repository)
    artifacts = ArtifactStore(
        paths.attachments,
        persistent_root=paths.artifacts,
        db_path=database,
        ttl_seconds=config.attachment_ttl_seconds,
    )
    registry = _build_registry(config, artifacts, memory, episodic)
    skills = SkillCatalog()
    skill_providers = _managed_skill_providers(managed, skills, packages)
    mcp_providers = _managed_mcp_providers(
        managed,
        secret_root=paths.mcp_secrets,
        packages=packages,
    )
    extensions = ExtensionManager(
        registry,
        (
            *skill_providers,
            *mcp_providers,
        ),
    )
    managed_extension_providers = {
        "current": (*skill_providers, *mcp_providers)
    }

    tool_step = ToolStep(
        registry,
        ToolArgumentPolicy(config.working_directory),
        prepare_execution=ensure_desktop_session,
    )
    async def observe_usage(
        request: ExecuteAgentTurn,
        event: UsageReported,
    ) -> None:
        usage = event.usage
        await asyncio.to_thread(
            llm_traces.record_call,
            principal_id=request.scope.principal_id,
            session_id=request.scope.session_handle,
            run_id=request.turn_id,
            client_request_id=request.client_request_id,
            model=str(
                usage.get("provider_model")
                or model_holder["current"].alias
            ),
            iteration=max(1, int(usage.get("iteration") or 1)),
            prompt_tokens=_usage_integer(usage, "prompt_tokens", "input_tokens"),
            completion_tokens=_usage_integer(
                usage, "completion_tokens", "output_tokens"
            ),
            cached_tokens=_cached_usage_tokens(usage),
            finish_reason=str(usage.get("finish_reason") or ""),
            tool_calls=max(0, int(usage.get("tool_calls") or 0)),
            requested_max_tokens=managed.operational.max_output_tokens,
            message_budget=prompt_budget,
            schema_tokens=max(
                0,
                int(usage.get("schema_tokens_estimated") or 0),
            ),
            prompt_tokens_estimated=max(
                0,
                int(usage.get("prompt_tokens_estimated") or 0),
            ),
            prompt_tokens_source=str(
                usage.get("prompt_tokens_source") or "unavailable"
            ),
            completion_tokens_source=str(
                usage.get("completion_tokens_source") or "unavailable"
            ),
            tool_selection_mode=str(usage.get("tool_selection_mode") or ""),
            tool_selection_hits=_usage_integer(usage, "tool_selection_hits"),
            schema_hits=_usage_integer(usage, "schema_hits"),
        )

    async def observe_turn(
        request: ExecuteAgentTurn,
        terminal: TurnFinished,
        tool_calls: int,
        elapsed_ms: float,
    ) -> None:
        await asyncio.to_thread(
            turn_traces.record_turn,
            principal_id=request.scope.principal_id,
            session_id=request.scope.session_handle,
            run_id=request.turn_id,
            client_request_id=request.client_request_id,
            user_input=request.input,
            outcome=terminal.status,
            iterations=1,
            tool_calls=tool_calls,
            elapsed_ms=elapsed_ms,
        )

    capability_gateway = CapabilityGateway(registry, tool_step, artifacts)

    async def platform_turn_context(
        scope: RuntimeScope,
        query: str,
        available_tools: frozenset[str],
        allowed_skills: frozenset[str],
    ) -> RuntimeTurnContext:
        effective_capabilities = frozenset(
            capability
            for tool_name in available_tools
            for policy in (registry.policy(tool_name),)
            if policy is not None
            for capability in policy.capabilities
        )
        core, relevant, episodes = await asyncio.gather(
            asyncio.to_thread(
                memory_repository.list_memories,
                scope.principal_id,
                importance="core",
                limit=12,
            ),
            asyncio.to_thread(
                memory_repository.search_memories,
                scope.principal_id,
                query,
                limit=5,
            ),
            asyncio.to_thread(
                memory_repository.recall_episodes,
                scope.principal_id,
                scope.session_handle,
                query,
                limit=3,
            ),
        )
        core_keys = {str(item["key"]) for item in core}
        return RuntimeTurnContext(
            core_memory=tuple(
                f"{item['key']}: {item['value']}" for item in core
            ),
            relevant_memory=tuple(
                f"{item['key']}: {item['value']}"
                for item in relevant
                if str(item["key"]) not in core_keys
            ),
            episodic_memory=tuple(str(item["summary"]) for item in episodes),
            skill_instructions=skills.active_context(
                query,
                available_tools=available_tools,
                capabilities=effective_capabilities,
                allowed_skills=allowed_skills,
            ),
        )

    capability_mcp_host = CapabilityMCPHost(
        capability_gateway,
        host=config.capability_mcp_host,
        port=config.capability_mcp_port,
    )
    runtimes, _primary, configured_model, reviewer_model_alias = (
        _build_agent_runtime_set(
            managed,
            bootstrap=config,
            paths=paths,
            capability_gateway=capability_gateway,
            provider_factory=provider_factory,
        )
    )
    model_holder["current"] = configured_model
    agent_manager = AgentManager(
        runtimes,
        default_agent=managed.agent_system.default_agent,
        enabled={
            agent_id: managed.agent_system.agents[agent_id].enabled
            for agent_id in runtimes
        },
        max_concurrency={
            agent_id: agent_resolver.runtime_spec(agent_id).max_concurrency
            for agent_id in runtimes
        },
        system_agents=frozenset(
            agent_id
            for agent_id in runtimes
            if agent_resolver.profile(agent_id).visibility == "system"
        ),
        generation_ids={
            agent_id: _agent_generation_id(managed, agent_id)
            for agent_id in runtimes
        },
    )
    agent_bindings = AgentSessionBindingRepository(database)
    invocation_policies = InvocationPolicyRepository(database)

    async def record_invocation_policy(request, policy) -> None:
        await asyncio.to_thread(
            invocation_policies.record,
            request.turn_id,
            request.scope.principal_id,
            request.scope.session_handle,
            policy,
        )

    def invocation_policy_for(turn_id: str):
        try:
            return invocation_policies.get(turn_id)
        except LookupError:
            return None

    execution_generation_barrier = asyncio.Lock()
    agent_execution = AgentExecutionService(
        agent_manager,
        agent_bindings,
        capability_gateway,
        artifacts,
        resolver_for=lambda: resolver_holder["current"],
        capabilities_for=capabilities_for_scope,
        installed_skills=lambda: frozenset(
            package.manifest.id for package in skills.packages
        ),
        policy_observer=record_invocation_policy,
        policy_snapshot_for=invocation_policy_for,
        external_mcp_endpoint=lambda: capability_mcp_host.endpoint,
        usage_observer=observe_usage,
        turn_observer=observe_turn,
        context_provider=platform_turn_context,
        generation_barrier=execution_generation_barrier,
    )
    approval_reviewer = KnoaReviewerAgent(
        agent_execution,
        sessions,
        agent_id=managed.approval_review.agent_id,
        model=reviewer_model_alias,
        timeout_seconds=managed.approval_review.timeout_seconds,
    )
    task_events = TaskEventHub()
    interaction_repository = HumanInteractionRepository(database)

    async def interaction_changed(interaction) -> None:
        if interaction.owner_kind == "conversation_turn":
            await conversation_service._notify(interaction.owner_id)
            return
        event = await asyncio.to_thread(
            tasks.append_event,
            interaction.principal_id,
            interaction.owner_id,
            (
                "interaction_requested"
                if interaction.state == "pending"
                else "interaction_resolved"
            ),
            TaskEventPayload(
                interaction_id=interaction.interaction_id,
                interaction_kind=interaction.kind,
                interaction_display=interaction.display,
                interaction_schema=interaction.resolution_schema,
            ),
        )
        await task_events.publish(event)

    interactions = HumanInteractionService(
        interaction_repository,
        changed=interaction_changed,
    )
    task_approvals = DurableApprovalService(
        tasks,
        task_events,
        reviewer=(
            approval_reviewer
            if managed.approval_review.mode != "off"
            else None
        ),
        review_mode=ApprovalReviewMode(managed.approval_review.mode),
        auto_max_risk=managed.approval_review.auto_max_risk,
    )
    task_tool_commits = DurableToolCommitService(tasks)
    task_executor = TaskExecutor(
        tasks,
        sessions,
        agent_execution,
        task_approvals,
        task_tool_commits,
        task_events,
        interactions=interactions.for_owner("task_execution"),
    )
    task_service = TaskService(
        tasks,
        task_executor,
        task_approvals,
        task_events,
        interactions=interactions,
    )
    conversations = ConversationRepository(
        database,
        detail_retention_seconds=(
            config.conversation_detail_retention_days * 24 * 60 * 60
        ),
    )
    conversation_service = ConversationService(
        sessions,
        conversations,
        agent_execution,
        interactions=interactions,
        approval_reviewer=(
            approval_reviewer
            if managed.approval_review.mode != "off"
            else None
        ),
        approval_review_mode=ApprovalReviewMode(managed.approval_review.mode),
        approval_auto_max_risk=managed.approval_review.auto_max_risk,
    )

    async def preflight_configuration(candidate: ManagedConfig) -> None:
        try:
            candidates, _primary, _model, _reviewer_model = (
                _build_agent_runtime_set(
                    candidate,
                    bootstrap=config,
                    paths=paths,
                    capability_gateway=capability_gateway,
                    provider_factory=provider_factory,
                )
            )
            health = await asyncio.gather(
                *(runtime.health_check() for runtime in candidates.values())
            )
            if any(not item.healthy for item in health):
                raise ConfigApplyError(
                    "runtime_preflight_failed",
                    "One or more Agent Runtime generations are unhealthy",
                )
            preflight_extensions = ExtensionManager(
                ToolRegistry(),
                (
                    *_managed_skill_providers(candidate, SkillCatalog(), packages),
                    *_managed_mcp_providers(
                        candidate,
                        secret_root=paths.mcp_secrets,
                        packages=packages,
                    ),
                ),
            )
            await preflight_extensions.start()
            try:
                failed = tuple(
                    status
                    for status in preflight_extensions.statuses
                    if status.state.value == "failed"
                )
                if failed:
                    raise ConfigApplyError(
                        "extension_preflight_failed",
                        failed[0].detail or "Extension preflight failed",
                    )
            finally:
                await preflight_extensions.stop()
        except ConfigApplyError:
            raise
        except Exception as exc:
            raise ConfigApplyError(
                "runtime_preflight_failed",
                str(exc),
            ) from exc

    async def apply_configuration_unlocked(previous, revision) -> None:
        candidate = revision.document
        previous_managed_providers = managed_extension_providers["current"]
        extension_swapped = False
        try:
            candidate_runtimes, _new_primary, new_model, reviewer_model = (
                _build_agent_runtime_set(
                    candidate,
                    bootstrap=config,
                    paths=paths,
                    capability_gateway=capability_gateway,
                    provider_factory=provider_factory,
                )
            )
            next_resolver = AgentDefinitionResolver(
                candidate.agent_system,
                config_revision_id=revision.revision_id,
            )
            extensions_changed = _extension_generation_id(
                previous.document
            ) != _extension_generation_id(candidate)
            next_managed_providers = previous_managed_providers
            if extensions_changed:
                next_managed_providers = (
                    *_managed_skill_providers(candidate, skills, packages),
                    *_managed_mcp_providers(
                        candidate,
                        secret_root=paths.mcp_secrets,
                        packages=packages,
                    ),
                )
                for provider in previous_managed_providers:
                    if isinstance(provider, MCPServerProvider):
                        await mcp_resource_tasks.remove_provider(provider)
                    await extensions.remove_provider(provider)
                added_providers = []
                try:
                    for provider in next_managed_providers:
                        status = await extensions.add_provider(provider)
                        if status.state.value == "failed":
                            raise ConfigApplyError(
                                "extension_apply_failed",
                                status.detail or "Extension failed to start",
                            )
                        added_providers.append(provider)
                    for provider in next_managed_providers:
                        if isinstance(provider, MCPServerProvider):
                            mcp_resource_tasks.add_provider(provider)
                except Exception:
                    for provider in reversed(added_providers):
                        if isinstance(provider, MCPServerProvider):
                            await mcp_resource_tasks.remove_provider(provider)
                        await extensions.remove_provider(provider)
                    for provider in previous_managed_providers:
                        await extensions.add_provider(provider)
                        if isinstance(provider, MCPServerProvider):
                            mcp_resource_tasks.add_provider(provider)
                    raise
                managed_extension_providers["current"] = next_managed_providers
                extension_swapped = True
            await agent_manager.replace_generations(
                candidate_runtimes,
                default_agent=candidate.agent_system.default_agent,
                enabled={
                    agent_id: candidate.agent_system.agents[agent_id].enabled
                    for agent_id in candidate_runtimes
                },
                max_concurrency={
                    agent_id: next_resolver.runtime_spec(agent_id).max_concurrency
                    for agent_id in candidate_runtimes
                },
                system_agents=frozenset(
                    agent_id
                    for agent_id in candidate_runtimes
                    if next_resolver.profile(agent_id).visibility == "system"
                ),
                generation_ids={
                    agent_id: _agent_generation_id(candidate, agent_id)
                    for agent_id in candidate_runtimes
                },
                drain_seconds=candidate.operational.generation_drain_seconds,
            )
        except Exception as exc:
            if extension_swapped:
                for provider in tuple(managed_extension_providers["current"]):
                    if isinstance(provider, MCPServerProvider):
                        await mcp_resource_tasks.remove_provider(provider)
                    await extensions.remove_provider(provider)
                for provider in previous_managed_providers:
                    await extensions.add_provider(provider)
                    if isinstance(provider, MCPServerProvider):
                        mcp_resource_tasks.add_provider(provider)
                managed_extension_providers["current"] = previous_managed_providers
            raise ConfigApplyError("runtime_apply_failed", str(exc)) from exc
        resolver_holder["current"] = next_resolver
        managed_holder["current"] = candidate
        model_holder["current"] = new_model
        next_reviewer = KnoaReviewerAgent(
            agent_execution,
            sessions,
            agent_id=candidate.approval_review.agent_id,
            model=reviewer_model,
            timeout_seconds=candidate.approval_review.timeout_seconds,
        )
        reviewer_port = (
            next_reviewer if candidate.approval_review.mode != "off" else None
        )
        review_mode = ApprovalReviewMode(candidate.approval_review.mode)
        task_approvals.configure_review(
            reviewer_port,
            review_mode,
            candidate.approval_review.auto_max_risk,
        )
        conversation_service.configure_approval_review(
            reviewer_port,
            review_mode,
            candidate.approval_review.auto_max_risk,
        )

    async def apply_configuration(previous, revision) -> None:
        async with execution_generation_barrier:
            await apply_configuration_unlocked(previous, revision)

    configuration = ConfigurationService(
        config_registry,
        managed,
        bootstrap_actor=config.owner_principal_id,
        preflight=preflight_configuration,
        applier=apply_configuration,
        normalizer=lambda candidate: _freeze_skill_digests(candidate, packages),
    )
    schedule_dispatcher = ScheduleDispatcher(schedules, task_service)
    schedule_service = ScheduleService(schedules, schedule_dispatcher)
    trigger_dispatcher = TriggerDispatcher(triggers, task_service)
    trigger_service = TriggerService(triggers, trigger_dispatcher)
    mcp_resource_tasks = MCPResourceTaskBridge(
        mcp_providers,
        task_service,
        sessions,
        trigger_service,
    )
    mcp_packages = MCPPackageService(
        paths.mcp,
        paths.cache / "mcp-imports",
        extensions,
        mcp_resource_tasks,
        (),
        reserved_ids=frozenset(managed.mcp_servers),
        secret_root=paths.mcp_secrets,
    )
    delegation_repository = DelegationRepository(database)
    delegations = DelegationService(
        delegation_repository,
        invocation_policies,
        sessions,
        task_service,
        tasks,
        conversations,
        capability_gateway,
        artifacts,
        resolver_for=lambda: resolver_holder["current"],
        capabilities_for=capabilities_for_scope,
        installed_skills=lambda: frozenset(
            package.manifest.id for package in skills.packages
        ),
    )
    registry.register(SpawnSubagentTool(delegations))
    registry.register(SubagentTool(delegations))
    registry.register(
        CreateTaskTool(sessions, task_service, schedule_service, trigger_service)
    )
    registry.register(
        TaskControlTool(
            sessions,
            task_service,
            schedule_service,
            trigger_service,
        )
    )
    config_controller = ConfigurationController(configuration)
    mcp_onboarding = MCPOnboardingService(
        extensions,
        config_controller,
        mcp_resource_tasks,
        mcp_providers,
    )
    registry.register(MCPInspectTool(mcp_onboarding))
    registry.register(MCPConnectTool(mcp_onboarding))
    registry.register(MCPDisableTool(mcp_onboarding))

    def status_details(scope: RuntimeScope) -> dict[str, object]:
        configured_model = model_holder["current"]
        llm = llm_traces.session_totals(
            scope.principal_id,
            scope.session_handle,
        )
        turns = turn_traces.session_totals(
            scope.principal_id,
            scope.session_handle,
        )
        return {
            "provider": configured_model.provider_name,
            **llm,
            **turns,
            "model": llm["model"] or configured_model.alias,
        }

    control = ControlService(
        sessions,
        memory_repository,
        tool_names=lambda scope: registry.list_for(capabilities_for_scope(scope)),
        tool_descriptors=lambda scope: tuple(
            ToolDescriptorRecord(
                name=descriptor.name,
                description=descriptor.description[:2000],
                origin_kind=descriptor.origin.kind.value,
                extension_id=descriptor.origin.extension_id,
                effect=descriptor.policy.effect.value,
                risk=descriptor.policy.risk.value,
                capabilities=tuple(
                    sorted(
                        capability.value
                        for capability in descriptor.policy.capabilities
                    )
                ),
                requires_confirmation=descriptor.requires_confirmation,
            )
            for descriptor in registry.descriptors_for(capabilities_for_scope(scope))
        ),
        config_controller=config_controller,
        status_details=status_details,
        extension_statuses=lambda: tuple(
            ExtensionStatusRecord(
                extension_id=status.descriptor.extension_id,
                kind=status.descriptor.kind.value,
                state=status.state.value,
                tools=status.tools,
                detail=status.detail,
            )
            for status in extensions.statuses
        ),
        mcp_resources=mcp_resource_tasks.catalog,
        agent_selector=agent_manager.resolve_agent_id,
    )
    artifact_service = ArtifactService(sessions, artifacts)
    transcription_service = ArtifactTranscriptionService(
        config.audio_transcription,
        artifacts,
        registry,
        tool_step,
        capabilities_for=capabilities_for_scope,
    )

    local_token = resolve_local_service_token(paths)
    credentials = {local_token: config.owner_principal_id}
    if config.service_token.strip():
        credentials[config.service_token.strip()] = "remote"

    async def preview_invocation_policy(principal_id: str, request):
        scope = RuntimeScope(principal_id=principal_id, session_handle="preview")
        principal_capabilities = capabilities_for_scope(scope)
        return resolver_holder["current"].resolve_policy(
            request.agent_id,
            invocation_kind=request.invocation_kind,
            caller_id=request.caller_id or principal_id,
            principal_capabilities=frozenset(
                capability.value for capability in principal_capabilities
            ),
            available_tools=capability_gateway.available_tool_names(
                principal_capabilities
            ),
            installed_skills=frozenset(
                package.manifest.id for package in skills.packages
            ),
            requested_tools=request.requested_tools,
            requested_skills=request.requested_skills,
        )

    tcp_server = CoreServer(
        task_service,
        schedule_service,
        trigger_service,
        control,
        artifact_service,
        CompositeAuthenticator(
            StaticTokenAuthenticator(credentials),
            SignedPrincipalAuthenticator(local_token),
        ),
        conversations=conversation_service,
        transcription=transcription_service,
        interactions=interactions,
        mcp_packages=None,
        sessions=sessions,
        owner_principal_id=config.owner_principal_id,
        configuration=configuration,
        generation_states=agent_manager.generation_state,
        policy_preview=preview_invocation_policy,
    )
    host = CoreServiceHost(
        tcp=TcpCoreEndpoint(
            tcp_server,
            config.service_host,
            config.service_port,
        )
    )
    return CoreRuntimeComposition(
        paths=paths,
        sessions=sessions,
        tasks=tasks,
        task_service=task_service,
        conversations=conversations,
        conversation_service=conversation_service,
        interactions=interactions,
        schedules=schedules,
        schedule_dispatcher=schedule_dispatcher,
        schedule_service=schedule_service,
        triggers=triggers,
        trigger_dispatcher=trigger_dispatcher,
        trigger_service=trigger_service,
        memory=memory_repository,
        artifacts=artifacts,
        registry=registry,
        agent_manager=agent_manager,
        agent_execution=agent_execution,
        agent_bindings=agent_bindings,
        invocation_policies=invocation_policies,
        delegation_repository=delegation_repository,
        delegations=delegations,
        capability_gateway=capability_gateway,
        capability_mcp_host=capability_mcp_host,
        control=control,
        artifact_service=artifact_service,
        transcription_service=transcription_service,
        llm_traces=llm_traces,
        turn_traces=turn_traces,
        extensions=extensions,
        mcp_resource_tasks=mcp_resource_tasks,
        mcp_packages=mcp_packages,
        skills=skills,
        config_registry=config_registry,
        configuration=configuration,
        host=host,
    )
