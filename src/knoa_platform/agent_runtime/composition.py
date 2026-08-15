"""Concrete composition root for the forward-only Core runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from knoa_agent import (
    ContextCheckpointRepository,
    DisabledToolSelector,
    KnoaAgentRuntime,
    ToolInventory,
)
from knoa_agent_contracts import RuntimeTurnContext, TurnFinished, UsageReported
from knoa_codex_agent import CodexAgentRuntime, CodexSessionRepository
from knoa_platform.agent_runtime.artifact_service import ArtifactService
from knoa_platform.agent_runtime.config_control import PersistentConfigController
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
    AgentExecutionService,
    AgentManager,
    AgentSessionBindingRepository,
    ExecuteAgentTurn,
)
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
from knoa_platform.config import AppConfig
from knoa_platform.context.memory_db import (
    ScopedEpisodicMemory,
    ScopedUserMemory,
    SQLiteMemoryRepository,
)
from knoa_platform.context.prompt import build_system_prompt
from knoa_platform.conversation import ConversationRepository, ConversationService
from knoa_platform.desktop_session import ensure_desktop_session
from knoa_platform.extensions import ExtensionManager
from knoa_platform.extensions.mcp import build_mcp_providers
from knoa_platform.extensions.mcp_onboarding import MCPOnboardingService
from knoa_platform.extensions.mcp_package import (
    MCPPackageService,
    build_mcp_package_providers,
)
from knoa_platform.extensions.mcp_resource_tasks import MCPResourceTaskBridge
from knoa_platform.extensions.skill import (
    SkillCatalog,
    build_skill_providers,
    builtin_skill_root,
)
from knoa_platform.interactions import (
    HumanInteractionRepository,
    HumanInteractionService,
)
from knoa_platform.observability.trace import LLMTraceRecorder, TurnRecorder
from knoa_platform.principal import converge_owner_principals, discover_owner_aliases
from knoa_platform.runtime import RuntimePaths
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
from knoa_platform.tools.base import ToolCapability
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
from knoa_platform.tools.mcp_deploy import MCPDeployTool
from knoa_platform.tools.memory_tool import MemoryTool
from knoa_platform.tools.mouse import MouseTool
from knoa_platform.tools.notification import NotificationTool
from knoa_platform.tools.press_key import PressKeyTool
from knoa_platform.tools.read_artifact import ReadArtifactTool
from knoa_platform.tools.read_file import ReadFileTool
from knoa_platform.tools.registry import ToolRegistry
from knoa_platform.tools.screenshot import ScreenshotTool
from knoa_platform.tools.shell import ShellTool
from knoa_platform.tools.task_control import TaskControlTool
from knoa_platform.tools.type_text import TypeTextTool
from knoa_platform.tools.weather import WeatherTool
from knoa_platform.tools.web_fetch import WebFetchTool
from knoa_platform.tools.web_search import WebSearchTool
from knoa_platform.tools.window import WindowTool
from knoa_platform.tools.write_file import WriteFileTool

PERSONAL_LOCAL_CAPABILITIES = frozenset(ToolCapability)
REMOTE_SCOPED_CAPABILITIES = frozenset({ToolCapability.NETWORK})


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
    runtime: KnoaAgentRuntime
    agent_manager: AgentManager
    agent_execution: AgentExecutionService
    agent_bindings: AgentSessionBindingRepository
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


def build_core_runtime(
    config: AppConfig,
    *,
    provider_factory: Callable[..., HttpModelProvider] = HttpModelProvider,
) -> CoreRuntimeComposition:
    """Build one Core graph without legacy agents or in-process service fallback."""

    paths = RuntimePaths.from_root(config.runtime_root)
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
    sessions = RuntimeSessionRepository(database)
    prompt_budget = max(
        256,
        config.effective_context_window_budget() - config.max_tokens,
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
    skill_roots = (
        builtin_skill_root(),
        paths.skills,
        *(paths.resolve(directory) for directory in config.skill_directories),
    )
    mcp_providers = build_mcp_providers(
        config.mcp_servers,
        secret_root=paths.mcp_secrets,
    )
    mcp_package_providers = build_mcp_package_providers(
        paths.mcp,
        excluded_ids=frozenset(config.mcp_servers),
        secret_root=paths.mcp_secrets,
    )
    extensions = ExtensionManager(
        registry,
        (
            *build_skill_providers(skill_roots, skills),
            *mcp_providers,
            *mcp_package_providers,
        ),
    )

    primary = provider_factory(config.resolve_model())
    provider: ModelProviderPort = primary
    fallback = config.resolve_fallback_model()
    fallback_provider: HttpModelProvider | None = None
    if fallback is not None:
        fallback_provider = provider_factory(fallback)
        provider = FailoverModelProvider(primary, fallback_provider)

    async def health_probe() -> HealthStatus:
        primary_health = await primary.health_check()
        if primary_health.healthy or fallback_provider is None:
            return primary_health
        fallback_health = await fallback_provider.health_check()
        if fallback_health.healthy:
            return HealthStatus(
                healthy=True,
                detail=f"Fallback model available: {fallback_provider.model_alias}",
            )
        return HealthStatus(healthy=False, detail="No configured model is available")

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
            model=str(usage.get("provider_model") or primary.model_alias),
            iteration=max(1, int(usage.get("iteration") or 1)),
            prompt_tokens=_usage_integer(usage, "prompt_tokens", "input_tokens"),
            completion_tokens=_usage_integer(
                usage, "completion_tokens", "output_tokens"
            ),
            cached_tokens=_cached_usage_tokens(usage),
            finish_reason=str(usage.get("finish_reason") or ""),
            tool_calls=max(0, int(usage.get("tool_calls") or 0)),
            requested_max_tokens=config.max_tokens,
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
            ),
        )

    runtime = KnoaAgentRuntime(
        provider,
        ContextCheckpointRepository(paths.data / "knoa-agent-context.db"),
        GatewayMCPConnector(capability_gateway),
        system_prompt=build_system_prompt(),
        health_probe=health_probe,
        max_iterations=config.max_iterations,
        max_tool_calls=config.max_total_tool_calls,
        max_output_tokens=config.max_tokens,
        temperature=config.llm_temperature,
        context_window=config.effective_context_window_budget(),
    )
    capability_mcp_host = CapabilityMCPHost(
        capability_gateway,
        host=config.capability_mcp_host,
        port=config.capability_mcp_port,
    )
    runtimes = {"knoa": runtime}
    reviewer_config = config.agents.get("reviewer_agent")
    reviewer_model_alias = (
        config.approval_review.model
        or (reviewer_config.model if reviewer_config is not None else "")
    )
    if reviewer_config is not None and reviewer_config.enabled:
        if not reviewer_model_alias:
            raise ValueError("Enabled reviewer_agent requires a model")
        reviewer_provider = provider_factory(config.resolve_model(reviewer_model_alias))

        async def reviewer_health_probe() -> HealthStatus:
            return await reviewer_provider.health_check()

        runtimes["reviewer_agent"] = KnoaAgentRuntime(
            reviewer_provider,
            ContextCheckpointRepository(
                paths.data / "knoa-reviewer-agent-context.db"
            ),
            GatewayMCPConnector(capability_gateway),
            system_prompt=APPROVAL_REVIEWER_SYSTEM_PROMPT,
            health_probe=reviewer_health_probe,
            max_iterations=1,
            max_tool_calls=1,
            max_output_tokens=config.approval_review.max_output_tokens,
            temperature=0.0,
            context_window=max(
                512,
                config.resolve_model(reviewer_model_alias).context_window
                or config.context_window_budget,
            ),
            agent_id="reviewer_agent",
            display_name="Knoa Reviewer",
            tool_inventory=ToolInventory(
                semantic_selector=DisabledToolSelector()
            ),
        )
    codex_config = config.agents.get("codex")
    if codex_config is not None and codex_config.enabled:
        codex_state = paths.resolve("agents/codex", default_parent=paths.root)
        codex_workspace = (
            paths.resolve(codex_config.cwd, default_parent=paths.root)
            if codex_config.cwd
            else codex_state / "workspace"
        )
        codex_workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        codex_home = (
            paths.resolve(codex_config.home, default_parent=paths.root)
            if codex_config.home
            else None
        )
        runtimes["codex"] = CodexAgentRuntime(
            CodexSessionRepository(codex_state / "sessions.db"),
            command=codex_config.command,
            home=codex_home,
            cwd=codex_workspace,
            model=codex_config.model,
            approval_policy=codex_config.approval_policy,
            sandbox=codex_config.sandbox,
            request_timeout_seconds=codex_config.request_timeout_seconds,
            max_line_bytes=codex_config.max_line_bytes,
            max_event_queue=codex_config.max_event_queue,
        )
    agent_manager = AgentManager(
        runtimes,
        default_agent=config.default_agent,
        enabled={
            agent_id: config.agents[agent_id].enabled
            for agent_id in runtimes
        },
        max_concurrency={
            agent_id: config.agents[agent_id].max_concurrency
            for agent_id in runtimes
        },
        system_agents=frozenset({"reviewer_agent"}) & runtimes.keys(),
    )
    approval_reviewer = (
        KnoaReviewerAgent(
            agent_manager,
            capability_gateway,
            agent_id=config.approval_review.agent,
            model=reviewer_model_alias,
            timeout_seconds=config.approval_review.timeout_seconds,
        )
        if config.approval_review.mode != "off"
        else None
    )
    agent_bindings = AgentSessionBindingRepository(database)
    agent_execution = AgentExecutionService(
        agent_manager,
        agent_bindings,
        capability_gateway,
        artifacts,
        capabilities_for=capabilities_for_scope,
        external_mcp_endpoint=lambda: capability_mcp_host.endpoint,
        usage_observer=observe_usage,
        turn_observer=observe_turn,
        context_provider=platform_turn_context,
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
        reviewer=approval_reviewer,
        review_mode=ApprovalReviewMode(config.approval_review.mode),
        auto_max_risk=config.approval_review.auto_max_risk,
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
        approval_reviewer=approval_reviewer,
        approval_review_mode=ApprovalReviewMode(config.approval_review.mode),
        approval_auto_max_risk=config.approval_review.auto_max_risk,
    )
    schedule_dispatcher = ScheduleDispatcher(schedules, task_service)
    schedule_service = ScheduleService(schedules, schedule_dispatcher)
    trigger_dispatcher = TriggerDispatcher(triggers, task_service)
    trigger_service = TriggerService(triggers, trigger_dispatcher)
    mcp_resource_tasks = MCPResourceTaskBridge(
        (*mcp_providers, *mcp_package_providers),
        task_service,
        sessions,
        trigger_service,
    )
    mcp_packages = MCPPackageService(
        paths.mcp,
        paths.cache / "mcp-imports",
        extensions,
        mcp_resource_tasks,
        mcp_package_providers,
        reserved_ids=frozenset(config.mcp_servers),
        secret_root=paths.mcp_secrets,
    )
    registry.register(MCPDeployTool(mcp_packages))
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
    config_path = (
        Path(config.source_config_path).expanduser().resolve()
        if config.source_config_path
        else paths.config / "local.yaml"
    )
    config_controller = PersistentConfigController(config, config_path)
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
        configured_model = config.resolve_model()
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
        mcp_packages=mcp_packages,
        sessions=sessions,
        owner_principal_id=config.owner_principal_id,
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
        runtime=runtime,
        agent_manager=agent_manager,
        agent_execution=agent_execution,
        agent_bindings=agent_bindings,
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
        host=host,
    )
