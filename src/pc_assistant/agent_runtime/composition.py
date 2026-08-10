"""Concrete composition root for the forward-only Core runtime."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pc_assistant.agent_runtime.artifact_service import ArtifactService
from pc_assistant.agent_runtime.config_control import PersistentConfigController
from pc_assistant.agent_runtime.contracts import (
    ExtensionStatusRecord,
    HealthStatus,
    RunRequest,
    RuntimeScope,
    ToolDescriptorRecord,
)
from pc_assistant.agent_runtime.control import ControlService
from pc_assistant.agent_runtime.http_provider import (
    FailoverModelProvider,
    HttpModelProvider,
)
from pc_assistant.agent_runtime.model_step import (
    ModelProviderPort,
    ModelStep,
    ModelStepResult,
)
from pc_assistant.agent_runtime.react_loop import (
    ReActContext,
    ReActLimits,
    ReActLoop,
    ReActOutcome,
)
from pc_assistant.agent_runtime.runtime import AgentRuntime, ArtifactMessageHydrator
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository
from pc_assistant.agent_runtime.tool_step import (
    ToolArgumentPolicy,
    ToolStep,
)
from pc_assistant.agent_runtime.transcription_service import (
    ArtifactTranscriptionService,
)
from pc_assistant.artifacts import ArtifactStore
from pc_assistant.automation import (
    ScheduleDispatcher,
    ScheduleRepository,
    ScheduleService,
    TriggerDispatcher,
    TriggerRepository,
    TriggerService,
)
from pc_assistant.config import AppConfig
from pc_assistant.context.memory_db import (
    SQLiteMemoryRepository,
    ScopedEpisodicMemory,
    ScopedUserMemory,
)
from pc_assistant.context.prompt import build_session_context, build_system_prompt
from pc_assistant.context.session_context import (
    SessionContextRepository,
    SessionContextService,
)
from pc_assistant.conversation import ConversationRepository, ConversationService
from pc_assistant.desktop_session import ensure_desktop_session
from pc_assistant.extensions import ExtensionManager
from pc_assistant.extensions.mcp import build_mcp_providers
from pc_assistant.extensions.mcp_package import (
    MCPPackageService,
    build_mcp_package_providers,
)
from pc_assistant.extensions.skill import (
    SkillCatalog,
    build_skill_providers,
    builtin_skill_root,
)
from pc_assistant.observability.trace import LLMTraceRecorder, TurnRecorder
from pc_assistant.runtime import RuntimePaths
from pc_assistant.principal import converge_owner_principals, discover_owner_aliases
from pc_assistant.service.core_host import (
    CoreServiceHost,
    TcpCoreEndpoint,
)
from pc_assistant.service.core_server import (
    CompositeAuthenticator,
    CoreServer,
    SignedPrincipalAuthenticator,
    StaticTokenAuthenticator,
)
from pc_assistant.service.credentials import resolve_local_service_token
from pc_assistant.tools.artifact_prepare import ArtifactPrepareTool
from pc_assistant.tools.base import ToolCapability
from pc_assistant.tools.clipboard import ClipboardTool
from pc_assistant.tools.create_task import CreateTaskTool
from pc_assistant.tools.describe_tool import DescribeTool
from pc_assistant.tools.exchange import ExchangeTool
from pc_assistant.tools.hotkey import HotkeyTool
from pc_assistant.tools.memory_tool import MemoryTool
from pc_assistant.tools.mcp_import import MCPImportTool
from pc_assistant.tools.mouse import MouseTool
from pc_assistant.tools.notification import NotificationTool
from pc_assistant.tools.press_key import PressKeyTool
from pc_assistant.tools.read_artifact import ReadArtifactTool
from pc_assistant.tools.read_file import ReadFileTool
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.tools.screenshot import ScreenshotTool
from pc_assistant.tools.schedule_task import ScheduleTaskTool
from pc_assistant.tools.shell import ShellTool
from pc_assistant.tools.type_text import TypeTextTool
from pc_assistant.tools.task_control import TaskControlTool
from pc_assistant.tools.weather import WeatherTool
from pc_assistant.tools.web_fetch import WebFetchTool
from pc_assistant.tools.web_search import WebSearchTool
from pc_assistant.tools.window import WindowTool
from pc_assistant.tools.write_file import WriteFileTool
from pc_assistant.tasks import (
    DurableApprovalService,
    DurableToolCommitService,
    TaskEventHub,
    TaskExecutor,
    TaskRepository,
    TaskService,
)


PERSONAL_LOCAL_CAPABILITIES = frozenset(ToolCapability)
REMOTE_SCOPED_CAPABILITIES = frozenset({ToolCapability.NETWORK})


@dataclass(frozen=True)
class CoreRuntimeComposition:
    """Owned runtime graph and its independently managed service host."""

    paths: RuntimePaths
    sessions: RuntimeSessionRepository
    session_context: SessionContextService
    tasks: TaskRepository
    task_service: TaskService
    conversations: ConversationRepository
    conversation_service: ConversationService
    schedules: ScheduleRepository
    schedule_dispatcher: ScheduleDispatcher
    schedule_service: ScheduleService
    triggers: TriggerRepository
    trigger_dispatcher: TriggerDispatcher
    trigger_service: TriggerService
    memory: SQLiteMemoryRepository
    artifacts: ArtifactStore
    registry: ToolRegistry
    runtime: AgentRuntime
    control: ControlService
    artifact_service: ArtifactService
    transcription_service: ArtifactTranscriptionService
    llm_traces: LLMTraceRecorder
    turn_traces: TurnRecorder
    extensions: ExtensionManager
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
        257,
        config.effective_context_window_budget() - config.max_tokens,
    )
    session_context = SessionContextService(
        SessionContextRepository(database),
        soft_token_limit=max(256, int(prompt_budget * 0.65)),
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
    extensions = ExtensionManager(
        registry,
        (
            *build_skill_providers(skill_roots, skills),
            *build_mcp_providers(config.mcp_servers),
            *build_mcp_package_providers(
                paths.mcp,
                excluded_ids=frozenset(config.mcp_servers),
            ),
        ),
    )
    mcp_packages = MCPPackageService(
        paths.mcp,
        paths.cache / "mcp-imports",
        extensions,
        reserved_ids=frozenset(config.mcp_servers),
    )
    registry.register(MCPImportTool(mcp_packages))

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

    async def runtime_context(scope: RuntimeScope, query: str) -> str:
        capabilities = capabilities_for_scope(scope)
        skill_context = skills.active_context(
            query,
            available_tools=frozenset(registry.list_for(capabilities)),
            capabilities=capabilities,
        )
        history_context = await asyncio.to_thread(session_context.context, scope)
        memory_sections = [
            memory.build_context_string(query=query),
            episodic.build_context_string(query=query),
        ]
        return build_session_context(
            session_history_context=history_context,
            memory_context="\n\n".join(
                section for section in memory_sections if section
            ),
            skill_context=skill_context,
        )

    model_step = ModelStep(
        provider,
        message_hydrator=ArtifactMessageHydrator(artifacts),
    )

    async def observe_model_step(
        context: ReActContext,
        iteration: int,
        result: ModelStepResult,
    ) -> None:
        usage = result.usage
        prompt_tokens = _usage_integer(usage, "prompt_tokens", "input_tokens")
        completion_tokens = _usage_integer(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        await asyncio.to_thread(
            llm_traces.record_call,
            principal_id=context.scope.principal_id,
            session_id=context.scope.session_handle,
            run_id=context.run_id,
            client_request_id=context.client_request_id,
            model=result.provider_model or primary.model_alias,
            iteration=iteration,
            prompt_tokens=prompt_tokens or result.prompt_tokens_estimated,
            completion_tokens=completion_tokens,
            cached_tokens=_cached_usage_tokens(usage),
            latency_ms=result.latency_ms,
            ttft_ms=result.ttft_ms,
            finish_reason=result.finish_reason,
            tool_calls=len(result.tool_calls),
            error=result.error_code,
            requested_max_tokens=config.max_tokens,
            message_budget=max(
                257,
                config.effective_context_window_budget() - config.max_tokens,
            ),
            schema_tokens=result.schema_tokens_estimated,
            failover_used=result.failover_used,
        )
    tool_step = ToolStep(
        registry,
        ToolArgumentPolicy(config.working_directory),
        prepare_execution=ensure_desktop_session,
    )
    react_loop = ReActLoop(
        model_step,
        tool_step,
        limits=ReActLimits(
            max_iterations=config.max_iterations,
            max_tool_calls=config.max_total_tool_calls,
        ),
        model_observer=observe_model_step,
    )

    async def observe_run(
        scope: RuntimeScope,
        run_id: str,
        request: RunRequest,
        outcome: ReActOutcome,
        elapsed_ms: float,
    ) -> None:
        await asyncio.to_thread(
            turn_traces.record_turn,
            principal_id=scope.principal_id,
            session_id=scope.session_handle,
            run_id=run_id,
            client_request_id=request.client_request_id,
            user_input=request.input,
            outcome=outcome.status,
            iterations=outcome.iterations,
            tool_calls=outcome.tool_calls,
            elapsed_ms=elapsed_ms,
        )
    runtime = AgentRuntime(
        react_loop,
        registry,
        artifacts,
        capabilities_for=capabilities_for_scope,
        health_probe=health_probe,
        system_prompt=build_system_prompt(),
        prompt_budget=prompt_budget,
        max_output_tokens=config.max_tokens,
        temperature=config.llm_temperature,
        runtime_context=runtime_context,
        run_observer=observe_run,
    )
    task_events = TaskEventHub()
    task_approvals = DurableApprovalService(tasks, task_events)
    task_tool_commits = DurableToolCommitService(tasks)
    task_executor = TaskExecutor(
        tasks,
        sessions,
        runtime,
        task_approvals,
        task_tool_commits,
        task_events,
        session_context=session_context,
    )
    task_service = TaskService(
        tasks,
        task_executor,
        task_approvals,
        task_events,
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
        runtime,
        session_context=session_context,
    )
    schedule_dispatcher = ScheduleDispatcher(schedules, task_service)
    schedule_service = ScheduleService(schedules, schedule_dispatcher)
    trigger_dispatcher = TriggerDispatcher(triggers, task_service)
    trigger_service = TriggerService(triggers, trigger_dispatcher)
    registry.register(CreateTaskTool(sessions, task_service))
    registry.register(ScheduleTaskTool(sessions, task_service, schedule_service))
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
                    sorted(capability.value for capability in descriptor.policy.capabilities)
                ),
                requires_confirmation=descriptor.requires_confirmation,
            )
            for descriptor in registry.descriptors_for(
                capabilities_for_scope(scope)
            )
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
        session_context=session_context,
        tasks=tasks,
        task_service=task_service,
        conversations=conversations,
        conversation_service=conversation_service,
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
        control=control,
        artifact_service=artifact_service,
        transcription_service=transcription_service,
        llm_traces=llm_traces,
        turn_traces=turn_traces,
        extensions=extensions,
        mcp_packages=mcp_packages,
        skills=skills,
        host=host,
    )
