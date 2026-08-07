from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from pc_assistant.artifacts import ArtifactStore
from pc_assistant.config import AppConfig, ResolvedModelConfig
from pc_assistant.context.cache import CachePlan, build_cache_plan
from pc_assistant.context.conversation import ConversationManager
from pc_assistant.context.evidence import EvidencePolicy
from pc_assistant.context.memory import ProceduralMemory, UserMemory
from pc_assistant.context.memory_db import (
    SQLiteMemoryRepository,
    ScopedEpisodicMemory,
    ScopedUserMemory,
)
from pc_assistant.context.prompt import build_system_prompt
from pc_assistant.context.session_db import SessionTranscriptRepository
from pc_assistant.context.token_estimate import TokenEstimator, normalize_family
from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.confirm import ConfirmFn
from pc_assistant.harness.executor import VerifiedToolExecutor
from pc_assistant.harness.idempotency import IdempotencyLog
from pc_assistant.harness.limiter import RateLimiter
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.harness.verifier import PostVerifyFn, Verifier
from pc_assistant.llm_provider import FailoverLLMProvider, LLMProvider
from pc_assistant.observability.trace import LLMTraceRecorder, TurnRecorder
from pc_assistant.planner import AgentPlanner
from pc_assistant.reflection import ReflectionChecker
from pc_assistant.runtime import RuntimePaths
from pc_assistant.session import SessionManager
from pc_assistant.tools.artifact_prepare import ArtifactPrepareTool
from pc_assistant.tools.clipboard import ClipboardTool
from pc_assistant.tools.describe_tool import DescribeTool
from pc_assistant.tools.exchange import ExchangeTool
from pc_assistant.tools.hotkey import HotkeyTool
from pc_assistant.tools.image_inspect import ImageInspectTool
from pc_assistant.tools.memory_tool import MemoryTool
from pc_assistant.tools.mouse import MouseTool
from pc_assistant.tools.notification import NotificationTool
from pc_assistant.tools.press_key import PressKeyTool
from pc_assistant.tools.read_file import ReadFileTool
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.tools.scheduler import SchedulerTool
from pc_assistant.tools.screen import ScreenTool
from pc_assistant.tools.screenshot import ScreenshotTool
from pc_assistant.tools.shell import ShellTool
from pc_assistant.tools.type_text import TypeTextTool
from pc_assistant.tools.ui import UITool
from pc_assistant.tools.weather import WeatherTool
from pc_assistant.tools.web_fetch import WebFetchTool
from pc_assistant.tools.web_search import WebSearchTool
from pc_assistant.tools.window import WindowTool
from pc_assistant.tools.write_file import WriteFileTool
from pc_assistant.vision.broker import VisionBroker


PostVerifyFactory = Callable[[AppConfig], PostVerifyFn]


@dataclass(frozen=True)
class FactoryOverrides:
    llm: LLMProvider | None = None
    conversation: ConversationManager | None = None
    memory: UserMemory | None = None
    safety: SafetyChecker | None = None
    registry: ToolRegistry | None = None
    limiter: RateLimiter | None = None
    audit: AuditLogger | None = None
    session_manager: SessionManager | None = None
    trace: LLMTraceRecorder | None = None
    turn_recorder: TurnRecorder | None = None
    evidence: EvidencePolicy | None = None
    artifact_store: ArtifactStore | None = None
    vision_llm: LLMProvider | None = None
    vision_broker: VisionBroker | None = None
    verifier: Verifier | None = None
    executor: VerifiedToolExecutor | None = None


@dataclass(frozen=True)
class ExecutionDependencies:
    generation: int
    tools_enabled: bool
    config: AppConfig
    resolved_model: ResolvedModelConfig
    resolved_fallback_model: ResolvedModelConfig | None
    resolved_vision_model: ResolvedModelConfig | None
    llm: LLMProvider
    planner: AgentPlanner
    token_estimator: TokenEstimator
    cache_plan: CachePlan
    reflection: ReflectionChecker | None
    vision_broker: VisionBroker | None
    registry: ToolRegistry
    tool_schemas: tuple[dict[str, Any], ...]
    verifier: Verifier
    executor: VerifiedToolExecutor


@dataclass(frozen=True)
class AgentDependencies:
    runtime_paths: RuntimePaths
    memory_repository: SQLiteMemoryRepository
    session_transcripts: SessionTranscriptRepository
    memory: UserMemory
    episodic_memory: ScopedEpisodicMemory
    procedural_memory: ProceduralMemory
    safety: SafetyChecker
    limiter: RateLimiter
    audit: AuditLogger
    idempotency: IdempotencyLog
    session_manager: SessionManager
    trace: LLMTraceRecorder
    turn_recorder: TurnRecorder
    evidence: EvidencePolicy
    artifact_store: ArtifactStore
    system_prompt: str
    execution: ExecutionDependencies


class AgentFactory:
    """The single concrete composition root for current Agent dependencies."""

    def __init__(
        self,
        config: AppConfig,
        confirm_callback: ConfirmFn | None = None,
        *,
        overrides: FactoryOverrides | None = None,
        max_sessions: int = 100,
        disable_tools: bool = False,
        runtime_consumer: Any = None,
        cleanup_session_assets: Callable[[str], None] | None = None,
        post_verify_factory: PostVerifyFactory | None = None,
    ) -> None:
        self._initial_config = self._validated_config(config)
        self._confirm_callback = confirm_callback
        self._overrides = overrides or FactoryOverrides()
        self._max_sessions = max(1, max_sessions)
        self._disable_tools = disable_tools
        self._runtime_consumer = runtime_consumer
        self._cleanup_session_assets = cleanup_session_assets
        self._post_verify_factory = post_verify_factory

    @property
    def overrides(self) -> FactoryOverrides:
        return self._overrides

    def build(self) -> AgentDependencies:
        config = self._validated_config(self._initial_config)
        runtime_paths = RuntimePaths.from_root(config.runtime_root)
        memory_repository = SQLiteMemoryRepository(runtime_paths.data / "assistant.db")
        session_transcripts = SessionTranscriptRepository(runtime_paths.data / "assistant.db")
        memory = self._overrides.memory or ScopedUserMemory(memory_repository)
        episodic_memory = ScopedEpisodicMemory(memory_repository)
        procedural_memory = ProceduralMemory(runtime_paths.data / "procedures")
        safety = self._overrides.safety or SafetyChecker(
            dangerous_commands=config.dangerous_commands,
            protected_paths=config.protected_paths,
            working_directory=config.working_directory,
        )
        limiter = self._overrides.limiter or RateLimiter()
        audit = self._overrides.audit or AuditLogger(
            log_dir=str(runtime_paths.logs / "audit"),
        )
        idempotency = IdempotencyLog(runtime_paths.cache / "idempotency.json")
        session_manager = self._overrides.session_manager or SessionManager(
            max_sessions=self._max_sessions,
        )
        if self._cleanup_session_assets is not None:
            session_manager.set_drop_callback(self._cleanup_session_assets)
        trace = self._overrides.trace or LLMTraceRecorder(
            path=str(runtime_paths.resolve(config.llm_trace_log)),
            enabled=config.trace_enabled,
        )
        turn_recorder = self._overrides.turn_recorder or TurnRecorder(
            path=str(runtime_paths.resolve(config.turn_trace_log)),
            enabled=config.trace_enabled,
        )
        evidence = self._overrides.evidence or EvidencePolicy(
            enabled=config.evidence_policy_enabled,
        )
        artifact_store = self._overrides.artifact_store or ArtifactStore(
            runtime_paths.attachments,
            persistent_root=runtime_paths.artifacts,
            db_path=runtime_paths.data / "assistant.db",
            ttl_seconds=config.attachment_ttl_seconds,
        )
        system_prompt = build_system_prompt()
        execution = self._build_execution_dependencies(
            config=config,
            generation=1,
            runtime_paths=runtime_paths,
            memory=memory,
            episodic_memory=episodic_memory,
            safety=safety,
            audit=audit,
            artifact_store=artifact_store,
            system_prompt=system_prompt,
            current=None,
            extra_tools=(),
            disable_tools=self._disable_tools,
            force_registry_rebuild=True,
        )
        return AgentDependencies(
            runtime_paths=runtime_paths,
            memory_repository=memory_repository,
            session_transcripts=session_transcripts,
            memory=memory,
            episodic_memory=episodic_memory,
            procedural_memory=procedural_memory,
            safety=safety,
            limiter=limiter,
            audit=audit,
            idempotency=idempotency,
            session_manager=session_manager,
            trace=trace,
            turn_recorder=turn_recorder,
            evidence=evidence,
            artifact_store=artifact_store,
            system_prompt=system_prompt,
            execution=execution,
        )

    def rebuild_execution_dependencies(
        self,
        current: ExecutionDependencies,
        candidate_config: AppConfig,
        dependencies: AgentDependencies,
        *,
        extra_tools: tuple[Any, ...] = (),
        disable_tools: bool | None = None,
        force_registry_rebuild: bool = False,
    ) -> ExecutionDependencies:
        config = self._validated_config(candidate_config)
        resolved_model = config.resolve_model()
        resolved_fallback_model = config.resolve_fallback_model()
        if (
            self._overrides.llm is not None
            and (
                resolved_model.model_dump() != current.resolved_model.model_dump()
                or self._model_dump(resolved_fallback_model)
                != self._model_dump(current.resolved_fallback_model)
            )
        ):
            raise ValueError("configuration would replace the injected model collaborator")
        if (
            self._overrides.vision_broker is not None
            and self._vision_binding_key(config)
            != self._vision_binding_key(current.config)
        ):
            raise ValueError("configuration would replace the injected vision collaborator")
        if (
            self._overrides.vision_llm is not None
            and config.vision_enabled
            and current.config.vision_enabled
            and self._model_dump(config.resolve_vision_model())
            != self._model_dump(current.resolved_vision_model)
        ):
            raise ValueError("configuration would replace the injected vision model collaborator")
        effective_disable_tools = self._disable_tools if disable_tools is None else disable_tools
        return self._build_execution_dependencies(
            config=config,
            generation=current.generation + 1,
            runtime_paths=dependencies.runtime_paths,
            memory=dependencies.memory,
            episodic_memory=dependencies.episodic_memory,
            safety=dependencies.safety,
            audit=dependencies.audit,
            artifact_store=dependencies.artifact_store,
            system_prompt=dependencies.system_prompt,
            current=current,
            extra_tools=extra_tools,
            disable_tools=effective_disable_tools,
            force_registry_rebuild=force_registry_rebuild,
        )

    @staticmethod
    def _validated_config(config: AppConfig) -> AppConfig:
        return AppConfig.model_validate(config.model_dump())

    @staticmethod
    def _model_dump(model: ResolvedModelConfig | None) -> dict[str, Any] | None:
        return model.model_dump() if model is not None else None

    @classmethod
    def _vision_binding_key(cls, config: AppConfig) -> tuple[Any, ...]:
        if not config.vision_enabled:
            return (False,)
        return (
            True,
            cls._model_dump(config.resolve_vision_model()),
            config.vision_max_tokens,
        )

    @staticmethod
    def _registry_binding_key(config: AppConfig) -> tuple[Any, ...]:
        return (
            config.working_directory,
            config.shell_timeout,
            config.ui_backend,
            config.screen_grid_enabled,
            config.vision_max_side,
            config.vision_jpeg_quality,
        )

    def _build_llm(
        self,
        config: AppConfig,
    ) -> tuple[ResolvedModelConfig, ResolvedModelConfig | None, LLMProvider]:
        model = config.resolve_model()
        fallback_model = config.resolve_fallback_model()
        if self._overrides.llm is not None:
            return model, fallback_model, self._overrides.llm
        primary = LLMProvider(
            server_url=model.server_url,
            model_name=model.model,
            provider=model.driver,
            api_key=model.api_key,
            api_base=model.api_base,
            timeout=model.timeout,
            supports_vision=model.supports_vision,
            thinking=model.thinking.model_dump() if model.thinking is not None else None,
        )
        fallback = None
        if fallback_model is not None and fallback_model.alias != model.alias:
            fallback = LLMProvider(
                server_url=fallback_model.server_url,
                model_name=fallback_model.model,
                provider=fallback_model.driver,
                api_key=fallback_model.api_key,
                api_base=fallback_model.api_base,
                timeout=fallback_model.timeout,
                supports_vision=fallback_model.supports_vision,
                thinking=(
                    fallback_model.thinking.model_dump()
                    if fallback_model.thinking is not None
                    else None
                ),
            )
        return model, fallback_model, FailoverLLMProvider(primary, fallback)

    def _build_vision(
        self,
        config: AppConfig,
        llm: LLMProvider,
        artifact_store: ArtifactStore,
        current: ExecutionDependencies | None,
    ) -> tuple[ResolvedModelConfig | None, VisionBroker | None]:
        if not config.vision_enabled:
            return None, self._overrides.vision_broker
        vision_model = config.resolve_vision_model()
        if llm.supports_vision:
            return vision_model, self._overrides.vision_broker
        if self._overrides.vision_broker is not None:
            return vision_model, self._overrides.vision_broker
        if (
            current is not None
            and current.vision_broker is not None
            and self._vision_binding_key(config)
            == self._vision_binding_key(current.config)
        ):
            return vision_model, current.vision_broker
        if self._overrides.vision_llm is not None:
            vision_llm = self._overrides.vision_llm
        else:
            vision_llm = LLMProvider(
                server_url=vision_model.server_url,
                model_name=vision_model.model,
                provider=vision_model.driver,
                api_key=vision_model.api_key,
                api_base=vision_model.api_base,
                timeout=vision_model.timeout,
                supports_vision=True,
                thinking=(
                    vision_model.thinking.model_dump()
                    if vision_model.thinking is not None
                    else None
                ),
            )
        return vision_model, VisionBroker(
            vision_llm,
            artifact_store,
            model_name=vision_model.model,
            max_tokens=config.vision_max_tokens,
        )

    def _build_execution_dependencies(
        self,
        *,
        config: AppConfig,
        generation: int,
        runtime_paths: RuntimePaths,
        memory: UserMemory,
        episodic_memory: ScopedEpisodicMemory,
        safety: SafetyChecker,
        audit: AuditLogger,
        artifact_store: ArtifactStore,
        system_prompt: str,
        current: ExecutionDependencies | None,
        extra_tools: tuple[Any, ...],
        disable_tools: bool,
        force_registry_rebuild: bool,
    ) -> ExecutionDependencies:
        resolved_model, fallback_model, llm = self._build_llm(config)
        vision_model, vision_broker = self._build_vision(
            config,
            llm,
            artifact_store,
            current,
        )
        needs_vision_tool = bool(config.vision_enabled and not llm.supports_vision and vision_broker)
        current_has_vision_tool = bool(
            current is not None and current.registry.get("inspect_image") is not None
        )
        vision_binding_changed = bool(
            current is not None
            and needs_vision_tool
            and current.vision_broker is not vision_broker
        )
        registry_config_changed = bool(
            current is not None
            and self._registry_binding_key(config)
            != self._registry_binding_key(current.config)
        )
        registry_change = (
            current is None
            or force_registry_rebuild
            or needs_vision_tool != current_has_vision_tool
            or vision_binding_changed
            or registry_config_changed
        )
        if registry_change and current is not None and any(
            value is not None
            for value in (
                self._overrides.registry,
                self._overrides.verifier,
                self._overrides.executor,
            )
        ):
            raise ValueError(
                "configuration would replace injected registry/verifier/executor bindings"
            )
        if registry_change:
            registry = self._build_registry(
                config=config,
                runtime_paths=runtime_paths,
                memory=memory,
                episodic_memory=episodic_memory,
                artifact_store=artifact_store,
                vision_broker=vision_broker if needs_vision_tool else None,
                extra_tools=extra_tools,
                disable_tools=disable_tools,
                use_override=current is None,
            )
            verifier = self._overrides.verifier or Verifier(
                safety=safety,
                registry=registry,
                audit=audit,
                confirm_callback=self._confirm_callback,
                verify_enabled=config.screen_verify_enabled,
                post_verify_callback=(
                    self._post_verify_factory(config)
                    if config.screen_verify_enabled and self._post_verify_factory is not None
                    else None
                ),
            )
            executor = self._overrides.executor or VerifiedToolExecutor(verifier, registry)
        else:
            registry = current.registry
            verifier = current.verifier
            executor = current.executor
        tool_schemas = tuple(
            copy.deepcopy(item["function"])
            for item in registry.all_schemas()
        )
        token_estimator = TokenEstimator(
            normalize_family(
                resolved_model.token_family or config.token_family,
                resolved_model.model,
            )
        )
        cache_plan = build_cache_plan(
            provider=resolved_model.driver,
            model=resolved_model.model,
            server_url=resolved_model.server_url,
            system_prompt=system_prompt,
            tool_schemas=[copy.deepcopy(schema) for schema in tool_schemas],
            estimator=token_estimator,
        )
        execution = ExecutionDependencies(
            generation=generation,
            tools_enabled=not disable_tools,
            config=config,
            resolved_model=resolved_model,
            resolved_fallback_model=fallback_model,
            resolved_vision_model=vision_model,
            llm=llm,
            planner=AgentPlanner(llm),
            token_estimator=token_estimator,
            cache_plan=cache_plan,
            reflection=(
                ReflectionChecker(llm, threshold=config.reflection_threshold)
                if config.reflection_enabled
                else None
            ),
            vision_broker=vision_broker,
            registry=registry,
            tool_schemas=tool_schemas,
            verifier=verifier,
            executor=executor,
        )
        self._validate_execution_dependencies(execution)
        return execution

    def _build_registry(
        self,
        *,
        config: AppConfig,
        runtime_paths: RuntimePaths,
        memory: UserMemory,
        episodic_memory: ScopedEpisodicMemory,
        artifact_store: ArtifactStore,
        vision_broker: VisionBroker | None,
        extra_tools: tuple[Any, ...],
        disable_tools: bool,
        use_override: bool,
    ) -> ToolRegistry:
        if use_override and self._overrides.registry is not None:
            return self._overrides.registry
        registry = ToolRegistry()
        if not disable_tools:
            tools = [
                ReadFileTool(working_directory=config.working_directory),
                WriteFileTool(working_directory=config.working_directory),
                ShellTool(default_timeout=config.shell_timeout),
                WebSearchTool(),
                WebFetchTool(),
                ClipboardTool(),
                MemoryTool(memory=memory, episodic=episodic_memory),
                WeatherTool(),
                ExchangeTool(),
                WindowTool(),
                NotificationTool(),
                UITool(
                    ui_backend=config.ui_backend,
                    artifact_dir=artifact_store.root / "screenshots",
                ),
                ScreenTool(
                    grid_enabled=config.screen_grid_enabled,
                    max_side=config.vision_max_side,
                    jpeg_quality=config.vision_jpeg_quality,
                    artifact_dir=artifact_store.root / "screenshots",
                ),
                PressKeyTool(),
                TypeTextTool(),
                HotkeyTool(),
                MouseTool(),
                SchedulerTool(runtime_paths.data / "assistant.db"),
                ScreenshotTool(artifact_store, artifact_store.root / "screenshots"),
                ArtifactPrepareTool(
                    artifact_store,
                    working_directory=config.working_directory,
                ),
            ]
            if vision_broker is not None:
                tools.append(ImageInspectTool(vision_broker))
            for tool in tools:
                registry.register(tool)
            for tool in extra_tools:
                registry.register(tool)
            registry.register(DescribeTool(registry=registry))
            scheduler = registry.get("schedule")
            if scheduler is not None and self._runtime_consumer is not None:
                scheduler.set_agent(self._runtime_consumer)
        return registry

    @staticmethod
    def _validate_execution_dependencies(execution: ExecutionDependencies) -> None:
        schemas = tuple(
            item["function"] for item in execution.registry.all_schemas()
        )
        if schemas != execution.tool_schemas:
            raise ValueError("tool schema snapshot does not match registry generation")
        if execution.verifier._registry is not execution.registry:
            raise ValueError("verifier is not bound to the candidate registry")
        if execution.executor._registry is not execution.registry:
            raise ValueError("executor is not bound to the candidate registry")
        if execution.executor._verifier is not execution.verifier:
            raise ValueError("executor is not bound to the candidate verifier")
        if (
            execution.tools_enabled
            and execution.config.vision_enabled
            and not execution.llm.supports_vision
            and execution.vision_broker is not None
        ):
            image_tool = execution.registry.get("inspect_image")
            if image_tool is None or image_tool._broker is not execution.vision_broker:
                raise ValueError("image tool is not bound to the candidate vision broker")
        if execution.cache_plan.model != execution.resolved_model.model:
            raise ValueError("cache plan model does not match resolved model identity")
