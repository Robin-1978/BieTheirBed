from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable

from pydantic import BaseModel

from pc_assistant.artifacts import ArtifactRef, ArtifactStore
from pc_assistant.config import AppConfig, load_config
from pc_assistant.context.assembly import assemble_llm_messages, truncate_messages
from pc_assistant.context.cache import build_cache_plan
from pc_assistant.context.conversation import ConversationManager
from pc_assistant.context.evidence import EvidencePolicy
from pc_assistant.context.llm_compact import compact_conversation_llm
from pc_assistant.context.memory import ProceduralMemory, UserMemory
from pc_assistant.context.memory_db import (
    SQLiteMemoryRepository,
    ScopedEpisodicMemory,
    ScopedUserMemory,
)
from pc_assistant.context.scope import derive_memory_scope, reset_memory_scope, set_memory_scope
from pc_assistant.context.session_db import SessionTranscriptRepository
from pc_assistant.context.prompt import build_system_prompt
from pc_assistant.context.token_estimate import TokenEstimator, normalize_family
from pc_assistant.harness.audit import AuditLogger
from pc_assistant.harness.confirm import ConfirmFn
from pc_assistant.harness.executor import VerifiedToolExecutor
from pc_assistant.harness.idempotency import IdempotencyLog
from pc_assistant.harness.limiter import RateLimiter
from pc_assistant.harness.refusal import RefusalCode, Verdict
from pc_assistant.harness.safety import SafetyChecker
from pc_assistant.harness.verifier import Verifier
from pc_assistant.llm_provider import LLMProvider, LLMResponse
from pc_assistant.logger import get_logger
from pc_assistant.model_adapter.types import ImageAttachment
from pc_assistant.observability.trace import LLMTraceRecorder, TurnRecorder
from pc_assistant.planner import AgentPlanner, StructuredPlan
from pc_assistant.reflection import ReflectionChecker
from pc_assistant.runtime import RuntimePaths
from pc_assistant.platform_ import get_platform
from pc_assistant.session import SessionManager, SessionState
from pc_assistant.tools.application import ApplicationTool
from pc_assistant.tools.clipboard import ClipboardTool
from pc_assistant.tools.filesystem import FilesystemTool
from pc_assistant.tools.registry import ToolRegistry
from pc_assistant.tools.session import SessionTool
from pc_assistant.tools.shell import ShellTool
from pc_assistant.tools.system import SystemTool
from pc_assistant.tools.web import WebTool
from pc_assistant.tools.memory_tool import MemoryTool
from pc_assistant.tools.weather import WeatherTool
from pc_assistant.tools.exchange import ExchangeTool
from pc_assistant.tools.window import WindowTool
from pc_assistant.tools.notification import NotificationTool
from pc_assistant.tools.keyboard import KeyboardTool
from pc_assistant.tools.mouse import MouseTool
from pc_assistant.tools.screen import ScreenTool
from pc_assistant.tools.ui import UITool
from pc_assistant.tools.scheduler import SchedulerTool
from pc_assistant.tools.describe_tool import DescribeTool
from pc_assistant.tools.image_inspect import ImageInspectTool
from pc_assistant.tools.artifact_prepare import ArtifactPrepareTool
from pc_assistant.tools.screenshot import ScreenshotTool
from pc_assistant.vision.broker import VisionBroker


# Tool-result payload cap for streamed events (bytes/chars). Keeps serialized
# websocket frames well under the protocol's WS_MAX_SIZE even when a tool
# returns a very large blob (e.g. reading a screenshot file).
_EVENT_RESULT_LIMIT = 100_000


def allocate_context_budget(
    context_window: int,
    schema_tokens: int,
    requested_completion_tokens: int,
) -> tuple[int, int]:
    """Split a provider window into input-history and completion budgets.

    A small context must never lose almost all of its input budget merely
    because ``max_tokens`` was configured optimistically. The completion
    reservation is reduced first, while retaining at least half of the
    remaining window for input whenever possible.
    """
    available = max(0, int(context_window) - max(0, int(schema_tokens)))
    if available <= 0:
        return 0, 0
    # Never reserve more than half of the usable window for completion. When
    # the requested completion is modest (e.g. 4K on a 50K model), all other
    # capacity remains available to system, memory, and conversation history.
    completion_budget = min(
        max(256, int(requested_completion_tokens)),
        max(256, available // 2),
    )
    if completion_budget > available:
        completion_budget = available
    input_budget = max(0, available - completion_budget)
    return input_budget, completion_budget


class AgentEvent(BaseModel):
    type: str
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = {}
    tool_result: Any = None
    artifact: ArtifactRef | None = None
    blocked: bool = False
    iteration: int = 0


def _strip_think_tags(text: str) -> tuple[str, str]:
    _think_open = re.compile(r"<think[^>]*>", re.IGNORECASE)
    _think_close = re.compile(r"</think[^>]*>", re.IGNORECASE)
    thinking = ""
    remaining = text
    while True:
        m_open = _think_open.search(remaining)
        if m_open is None:
            break
        m_close = _think_close.search(remaining, m_open.end())
        if m_close is None:
            thinking += remaining[m_open.end():]
            remaining = remaining[:m_open.start()]
            break
        thinking += remaining[m_open.end():m_close.start()]
        remaining = remaining[:m_open.start()] + remaining[m_close.end():]
    return remaining.strip(), thinking.strip()


class Agent:
    def __init__(
        self,
        config: AppConfig | None = None,
        confirm_callback: ConfirmFn | None = None,
        *,
        llm: LLMProvider | None = None,
        conversation: ConversationManager | None = None,
        memory: UserMemory | None = None,
        safety: SafetyChecker | None = None,
        registry: ToolRegistry | None = None,
        limiter: RateLimiter | None = None,
        audit: AuditLogger | None = None,
        max_sessions: int = 100,
        session_manager: SessionManager | None = None,
        trace: LLMTraceRecorder | None = None,
        turn_recorder: TurnRecorder | None = None,
        evidence: EvidencePolicy | None = None,
        artifact_store: ArtifactStore | None = None,
        vision_llm: LLMProvider | None = None,
        vision_broker: VisionBroker | None = None,
        disable_tools: bool = False,
    ) -> None:
        self._config = config or load_config()
        self._runtime_paths = RuntimePaths.from_root(self._config.runtime_root)
        self._logger = get_logger("agent")
        self._main_model = self._config.resolve_model()
        self._llm_injected = llm is not None
        self._llm = llm if llm is not None else LLMProvider(
            server_url=self._main_model.server_url,
            model_name=self._main_model.model,
            provider=self._main_model.driver,
            api_key=self._main_model.api_key,
            api_base=self._main_model.api_base,
            timeout=self._main_model.timeout,
            supports_vision=self._main_model.supports_vision,
            thinking=(
                self._main_model.thinking.model_dump()
                if self._main_model.thinking is not None
                else None
            ),
        )
        self._memory_repository = SQLiteMemoryRepository(
            self._runtime_paths.data / "assistant.db",
        )
        self._session_transcripts = SessionTranscriptRepository(
            self._runtime_paths.data / "assistant.db",
        )
        self._memory = memory if memory is not None else ScopedUserMemory(
            self._memory_repository,
        )
        self._episodic_memory = ScopedEpisodicMemory(self._memory_repository)
        self._procedural_memory = ProceduralMemory(
            self._runtime_paths.data / "procedures",
        )
        self._safety = safety if safety is not None else SafetyChecker(
            dangerous_commands=self._config.dangerous_commands,
            protected_paths=self._config.protected_paths,
            working_directory=self._config.working_directory,
        )
        self._registry = registry if registry is not None else ToolRegistry()
        self._limiter = limiter if limiter is not None else RateLimiter()
        self._audit = audit if audit is not None else AuditLogger(
            log_dir=str(self._runtime_paths.logs / "audit"),
        )
        self._confirm_callback = confirm_callback
        self._verifier = Verifier(
            safety=self._safety,
            registry=self._registry,
            audit=self._audit,
            confirm_callback=confirm_callback,
            verify_enabled=self._config.screen_verify_enabled,
            post_verify_callback=self._post_verify_screen if self._config.screen_verify_enabled else None,
        )
        self._executor = VerifiedToolExecutor(self._verifier, self._registry)
        self._idempotency = IdempotencyLog(
            storage_path=self._runtime_paths.cache / "idempotency.json",
        )
        self._planner = AgentPlanner(self._llm)
        self._reflection = ReflectionChecker(
            self._llm,
            threshold=self._config.reflection_threshold,
        ) if self._config.reflection_enabled else None
        self._connected = False
        self._system_prompt = build_system_prompt(
            working_directory=self._config.working_directory,
        )
        self._token_estimator = TokenEstimator(
            normalize_family(
                self._main_model.token_family or self._config.token_family,
                self._main_model.model,
            ),
        )

        self._session_manager = session_manager or SessionManager(
            max_sessions=max(max_sessions, 1),
        )
        self._default_state = self._session_manager.get("", self._system_prompt)
        if conversation is not None:
            self._default_state.conversation = conversation
        else:
            self._restore_session_transcript(self._default_state)
        self._trace = trace or LLMTraceRecorder(
            path=str(self._runtime_paths.resolve(self._config.llm_trace_log)),
            enabled=self._config.trace_enabled,
        )
        self._turn_recorder = turn_recorder or TurnRecorder(
            path=str(self._runtime_paths.resolve(self._config.turn_trace_log)),
            enabled=self._config.trace_enabled,
        )
        self._evidence = evidence or EvidencePolicy(enabled=self._config.evidence_policy_enabled)
        self._artifact_store = artifact_store or ArtifactStore(
            self._runtime_paths.attachments,
            persistent_root=self._runtime_paths.artifacts,
            db_path=self._runtime_paths.data / "assistant.db",
            ttl_seconds=self._config.attachment_ttl_seconds,
        )
        self._vision_broker: VisionBroker | None = None
        if self._config.vision_enabled and not self._llm.supports_vision:
            vision_model = self._config.resolve_vision_model()
            dedicated_vision_llm = vision_llm or LLMProvider(
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
            self._vision_broker = vision_broker or VisionBroker(
                dedicated_vision_llm,
                self._artifact_store,
                model_name=vision_model.model,
                max_tokens=self._config.vision_max_tokens,
            )
        self._session_manager.set_drop_callback(self._cleanup_session_assets)
        self._register_builtin_tools(disable_tools=disable_tools)
        self._cache_plan = build_cache_plan(
            provider=self._main_model.driver,
            model=self._main_model.model,
            server_url=self._main_model.server_url,
            system_prompt=self._system_prompt,
            tool_schemas=[t["function"] for t in self._registry.all_schemas()],
            estimator=self._token_estimator,
        )
        # Loop detection
        self._max_consecutive_same_tool = self._config.max_consecutive_same_tool

    # ------------------------------------------------------------------
    # Backward-compatible attribute mirrors (default session)
    # ------------------------------------------------------------------

    @property
    def conversation(self) -> ConversationManager:
        return self._default_state.conversation

    @property
    def _conversation(self) -> ConversationManager:
        return self._default_state.conversation

    @_conversation.setter
    def _conversation(self, value: ConversationManager) -> None:
        self._default_state.conversation = value

    @property
    def _cancelled(self) -> bool:
        return self._default_state.cancelled

    @_cancelled.setter
    def _cancelled(self, value: bool) -> None:
        self._default_state.cancelled = value

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def memory(self) -> UserMemory:
        return self._memory

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def _get_state(self, session_id: str) -> SessionState:
        if not session_id:
            return self._default_state
        is_new = not self._session_manager.has(session_id)
        state = self._session_manager.get(session_id, self._system_prompt)
        if is_new:
            self._restore_session_transcript(state)
        return state

    def _restore_session_transcript(self, state: SessionState) -> None:
        messages = self._session_transcripts.load(state.session_id)
        if messages:
            state.conversation.rebuild_from_messages(messages)

    def _persist_session_transcript(self, state: SessionState) -> None:
        self._session_transcripts.save(
            state.session_id,
            state.conversation.get_messages(),
        )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, session_id: str = "") -> None:
        state = self._get_state(session_id)
        state.cancelled = True
        self._llm.cancel(session_id or None)
        if state.tool_task is not None and not state.tool_task.done():
            state.tool_task.cancel()

    def cancel_session(self, session_id: str) -> None:
        self.cancel(session_id)

    def reset_cancelled(self) -> None:
        self._default_state.cancelled = False
        self._llm.reset_cancelled()

    # ------------------------------------------------------------------
    # Tool loop detection
    # ------------------------------------------------------------------

    def _check_tool_loop(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        history: list[str],
    ) -> tuple[bool, str]:
        """Check if we're in a tool calling loop. Returns (is_loop, reason).

        Only detects TRUE loops - same tool + same arguments repeatedly.
        Different arguments for same tool is NOT a loop (e.g., checking weather for multiple cities).
        """
        try:
            args_str = json.dumps(arguments, sort_keys=True)
        except Exception:
            args_str = str(sorted(arguments.items()))

        call_sig = f"{tool_name}:{args_str[:200]}"

        history.append(call_sig)
        if len(history) > 20:
            history.pop(0)

        if len(history) >= self._max_consecutive_same_tool:
            recent = history[-self._max_consecutive_same_tool:]
            if all(t == call_sig for t in recent):
                return True, f"Same tool '{tool_name}' with identical arguments called {self._max_consecutive_same_tool} times consecutively"

        return False, ""

    def _smart_truncate(self, result_str: str, tool_name: str, result: Any) -> str:
        """Smart truncation for long tool outputs."""
        max_chars = 3000

        if len(result_str) <= max_chars:
            return result_str

        if tool_name == "application" and isinstance(result, dict):
            processes = result.get("processes", [])
            if processes:
                sorted_by_cpu = sorted(processes, key=lambda x: x.get("cpu_percent", 0) or 0, reverse=True)
                top_cpu = sorted_by_cpu[:10]
                sorted_by_mem = sorted(processes, key=lambda x: x.get("memory_percent", 0) or 0, reverse=True)
                top_mem = sorted_by_mem[:10]

                summary = [
                    f"Total processes: {len(processes)}",
                    "",
                    "Top 10 by CPU%:",
                ]
                for p in top_cpu:
                    name = p.get("name", "unknown")
                    cpu = p.get("cpu_percent", 0)
                    pid = p.get("pid", "?")
                    summary.append(f"  {pid:>6} {name:<30} {cpu:>5.1f}%")

                summary.append("")
                summary.append("Top 10 by Memory%:")
                for p in top_mem:
                    name = p.get("name", "unknown")
                    mem = p.get("memory_percent", 0)
                    pid = p.get("pid", "?")
                    summary.append(f"  {pid:>6} {name:<30} {mem:>5.1f}%")

                summary_str = "\n".join(summary)
                if len(summary_str) <= max_chars:
                    return summary_str + f"\n\n[Truncated: showing top processes. Total: {len(processes)}]"
                return summary_str[:max_chars - 50] + f"\n\n[Truncated from {len(processes)} processes]"

            matches = result.get("matches", [])
            if matches:
                return result_str[:max_chars] + f"\n\n[Showing {len(matches)} matching processes]"

            if result.get("process"):
                return result_str[:max_chars]

        head_size = max_chars * 2 // 3
        tail_size = max_chars - head_size
        omitted = len(result_str) - max_chars
        return (
            result_str[:head_size]
            + f"\n\n... [{omitted} chars omitted] ...\n\n"
            + result_str[-tail_size:]
        )

    def _bounded_event_result(self, result: Any, result_str: str) -> Any:
        """Bound the ``tool_result`` payload attached to streamed events.

        The conversation copy is truncated by :meth:`_smart_truncate`, but the
        raw ``result`` rides along in the event and gets serialized into a
        websocket frame. Oversized results (e.g. reading a screenshot file)
        would exceed the frame limit and silently drop the connection, so cap
        the event payload while keeping small structured results intact
        (consumers rely on ``dict`` shape for error detection).
        """
        result = self._event_safe_result(result)
        if isinstance(result, str):
            return result if len(result) <= _EVENT_RESULT_LIMIT else result[: _EVENT_RESULT_LIMIT]
        try:
            size = len(str(result))
        except Exception:
            return result
        if size <= _EVENT_RESULT_LIMIT:
            return result
        return {"truncated": True, "size": size, "content": result_str[: _EVENT_RESULT_LIMIT]}

    def _resolve_attachments(
        self,
        session_id: str,
        attachments: list[ImageAttachment] | None,
    ) -> list[dict[str, Any]]:
        """Convert user image attachments into neutral content blocks."""
        if not attachments:
            return []
        from pc_assistant.vision.preprocess import image_block_from_file

        blocks: list[dict[str, Any]] = []
        for att in attachments:
            block = None
            if att.artifact_id:
                try:
                    block = self._artifact_store.reference(
                        session_id,
                        att.artifact_id,
                        caption=att.caption,
                    )
                except KeyError:
                    block = None
            elif att.data_url:
                try:
                    block = self._artifact_store.put_data_url(
                        session_id,
                        att.data_url,
                        media_type=att.media_type,
                        source="upload",
                        caption=att.caption,
                    )
                except ValueError:
                    block = None
            elif att.path:
                attachment_path = Path(att.path).expanduser().resolve()
                try:
                    attachment_path.relative_to(self._artifact_store.root.resolve())
                    block = self._artifact_store.register_path(
                        session_id,
                        attachment_path,
                        media_type=att.media_type,
                        source="managed-file",
                        caption=att.caption,
                    )
                except (ValueError, OSError):
                    image_block = image_block_from_file(
                        attachment_path,
                        max_side=self._config.vision_max_side,
                        quality=self._config.vision_jpeg_quality,
                    )
                    if image_block is None:
                        block = None
                    else:
                        try:
                            block = self._artifact_store.put_data_url(
                                session_id,
                                image_block["image_url"],
                                media_type=image_block.get("media_type", att.media_type),
                                source="file",
                                caption=att.caption,
                            )
                        except ValueError:
                            block = None
            if block is None:
                continue
            if att.caption:
                blocks.append({"type": "text", "text": f"[Image: {att.caption}]"})
            blocks.append(block)
        return blocks

    def store_artifact(self, session_id: str, attachment: ImageAttachment) -> dict[str, Any]:
        """Store one inbound image artifact and return reference metadata."""
        blocks = self._resolve_attachments(session_id, [attachment])
        ref = next(
            (block for block in blocks if block.get("type") == "image_ref"),
            None,
        )
        if ref is None:
            raise ValueError("Could not store attachment")
        return ref

    def _inline_image_blocks(
        self,
        session_id: str,
        tool_name: str,
        result: Any,
    ) -> list[dict[str, Any]] | None:
        """Extract an inline image block from a tool result, if present.

        Returns ``None`` when the result carries no image. The stored reference
        is hydrated for a multimodal main model or manifested for a text model.
        """
        if not isinstance(result, dict):
            return None
        block = result.get("image")
        if not isinstance(block, dict) or block.get("type") != "image":
            return None
        path = result.get("path", "")
        try:
            ref = self._artifact_store.register_path(
                session_id,
                path,
                media_type=str(block.get("media_type", "image/png")),
                source=f"tool:{tool_name}",
            )
        except ValueError:
            try:
                ref = self._artifact_store.put_data_url(
                    session_id,
                    str(block.get("image_url", "")),
                    media_type=str(block.get("media_type", "image/png")),
                    source=f"tool:{tool_name}",
                )
            except ValueError:
                return None
        return [
            {
                "type": "text",
                "text": (
                    f"[inline image from {tool_name}: {path}. "
                    "If the main model is text-only, call image_inspect with the manifested image_id "
                    "before making claims about visible content.]"
                ),
            },
            ref,
        ]

    @classmethod
    def _event_safe_result(cls, value: Any) -> Any:
        """Remove binary image encodings from events and persistence payloads."""
        if isinstance(value, str):
            return re.sub(
                r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+",
                "[binary image omitted]",
                value,
            )
        if isinstance(value, list):
            return [cls._event_safe_result(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._event_safe_result(item) for key, item in value.items()}
        return value

    @classmethod
    def _contains_binary_image(cls, value: Any) -> bool:
        if isinstance(value, str):
            return value.startswith("data:image/")
        if isinstance(value, list):
            return any(cls._contains_binary_image(item) for item in value)
        if isinstance(value, dict):
            return any(cls._contains_binary_image(item) for item in value.values())
        return False

    def _inline_image_note(self, result: Any) -> str:
        path = result.get("path", "") if isinstance(result, dict) else ""
        return f"[inline image captured: {path}]"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        state = self._default_state
        return {
            "provider": self._main_model.provider_name,
            "model": self._main_model.alias,
            "upstream_model": self._main_model.model or "default",
            "status": state.status,
            "connected": self._connected,
            "platform": get_platform(),
            "total_prompt_tokens": state.total_prompt_tokens,
            "total_completion_tokens": state.total_completion_tokens,
            "total_tokens": state.total_prompt_tokens + state.total_completion_tokens,
            "total_iterations": state.total_iterations,
            "conversation_turns": len([m for m in state.conversation.get_messages() if m["role"] == "user"]),
            "memory_items": len(self._memory),
            "tools": self._registry.list_tools(),
            "working_directory": self._config.working_directory,
            "active_sessions": len(self.session_stats()),
        }

    def session_stats(self) -> list[dict[str, Any]]:
        sessions = self._session_manager.stats()
        return [s for s in sessions if s.get("session_id")]

    def drop_session(self, session_id: str) -> None:
        """Drop a named session's state (its conversation history)."""
        if session_id:
            self._session_manager.drop(session_id)
            self._session_transcripts.delete(session_id)

    def compact_session(self, session_id: str = "", *, keep_recent: int = 4) -> None:
        """Mechanically compact one session without deleting its history."""
        state = self._get_state(session_id)
        state.conversation.compress(keep_recent=keep_recent)
        self._persist_session_transcript(state)

    def apply_config_change(self, field_name: str = "") -> dict[str, Any]:
        """Apply runtime config changes, rebuilding provider state when needed.

        Scalar execution/context limits are read on every turn. Provider
        identity and credentials require reconstructing the transport and
        cache plan, which is safe between turns and avoids a daemon restart.
        Injected test/custom providers are never replaced implicitly.
        """
        dynamic = {
            "max_iterations", "max_tokens", "max_total_tool_calls",
            "max_consecutive_tool_calls", "max_consecutive_same_tool",
            "context_window_budget", "llm_temperature", "llm_compact_enabled",
            "auto_compact_enabled", "auto_compact_threshold",
            "trace_enabled", "vision_max_side", "vision_jpeg_quality",
        }
        provider_fields = {
            "llm_provider", "llm_server_url", "llm_model_name", "llm_api_key",
            "llm_api_base", "default_model", "token_family",
        }
        if field_name in dynamic:
            return {"applied": True, "field": field_name, "restart_required": False}
        if field_name not in provider_fields:
            return {"applied": False, "field": field_name, "restart_required": True}
        if self._llm_injected:
            return {"applied": False, "field": field_name, "restart_required": True}

        model = self._config.resolve_model()
        candidate = LLMProvider(
            server_url=model.server_url,
            model_name=model.model,
            provider=model.driver,
            api_key=model.api_key,
            api_base=model.api_base,
            timeout=model.timeout,
            supports_vision=model.supports_vision,
            thinking=model.thinking.model_dump() if model.thinking is not None else None,
        )
        if candidate.supports_vision != self._llm.supports_vision:
            return {"applied": False, "field": field_name, "restart_required": True}
        self._main_model = model
        self._llm = candidate
        self._token_estimator = TokenEstimator(
            normalize_family(model.token_family or self._config.token_family, model.model),
        )
        self._cache_plan = build_cache_plan(
            provider=model.driver,
            model=model.model,
            server_url=model.server_url,
            system_prompt=self._system_prompt,
            tool_schemas=[t["function"] for t in self._registry.all_schemas()],
            estimator=self._token_estimator,
        )
        self._planner = AgentPlanner(self._llm)
        if self._reflection is not None:
            self._reflection = ReflectionChecker(
                self._llm,
                threshold=self._config.reflection_threshold,
            )
        return {"applied": True, "field": field_name, "restart_required": False}

    def session_messages(self, session_id: str = "") -> list[dict[str, Any]]:
        return self._get_state(session_id).conversation.get_messages()

    def cleanup_artifacts(self) -> None:
        self._artifact_store.cleanup_expired()

    def _cleanup_session_assets(self, session_id: str) -> None:
        self._artifact_store.cleanup_session(session_id)

    def resolve_artifact(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        """Resolve an opaque artifact ID for an in-process delivery adapter."""
        return self._artifact_store.resolve(session_id, artifact_id)

    def mark_artifact_delivered(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        """Record successful client delivery without delegating cleanup authority."""
        return self._artifact_store.mark_delivered(session_id, artifact_id)

    def clear_tools(self) -> None:
        """Unregister all tools (headless / benchmark no-tools mode)."""
        self._registry.clear()

    async def health_check(self) -> bool:
        result = await self._llm.health_check()
        self._connected = result
        return result

    def _post_verify_screen(self, tool_name: str, arguments: dict[str, Any]) -> Awaitable[str]:
        """Advisory post-action screen capture for the verifier's post-verify rule."""
        from pc_assistant.vision.preprocess import capture_block

        async def _verify() -> str:
            block = await asyncio.to_thread(
                capture_block,
                None,
                max_side=self._config.vision_max_side,
                quality=self._config.vision_jpeg_quality,
            )
            if block is None:
                return "post-verify: screen capture unavailable"
            w = block.get("width") or 0
            h = block.get("height") or 0
            return f"post-verify: captured {w}x{h} screen after {tool_name}({arguments.get('action', '?')})"

        return _verify()

    async def verify_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        confirm_callback: ConfirmFn | None = None,
    ) -> Verdict:
        return await self._verifier.verify(tool_name, arguments, confirm_callback=confirm_callback)

    def _register_builtin_tools(self, *, disable_tools: bool = False) -> None:
        if disable_tools:
            return
        builtin_tools = [
            FilesystemTool(working_directory=self._config.working_directory),
            ShellTool(default_timeout=self._config.shell_timeout),
            ApplicationTool(),
            WebTool(),
            SystemTool(),
            SessionTool(),
            ClipboardTool(),
            MemoryTool(memory=self._memory, episodic=self._episodic_memory),
            WeatherTool(),
            ExchangeTool(),
            WindowTool(),
            NotificationTool(),
            UITool(
                ui_backend=self._config.ui_backend,
                artifact_dir=self._artifact_store.root / "screenshots",
            ),
            ScreenTool(
                grid_enabled=self._config.screen_grid_enabled,
                max_side=self._config.vision_max_side,
                jpeg_quality=self._config.vision_jpeg_quality,
                artifact_dir=self._artifact_store.root / "screenshots",
            ),
            KeyboardTool(),
            MouseTool(),
            SchedulerTool(self._runtime_paths.data / "assistant.db"),
            ScreenshotTool(
                self._artifact_store,
                self._artifact_store.root / "screenshots",
            ),
            ArtifactPrepareTool(
                self._artifact_store,
                working_directory=self._config.working_directory,
            ),
        ]
        if self._vision_broker is not None:
            builtin_tools.append(ImageInspectTool(self._vision_broker))
        builtin_tools.append(DescribeTool(registry=self._registry))
        for tool in builtin_tools:
            self._registry.register(tool)

        scheduler = self._registry.get("scheduler")
        if scheduler is not None:
            scheduler.set_agent(self)

    def register_tool(self, tool: Any) -> None:
        self._registry.register(tool)

    @staticmethod
    def _ensure_system_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return messages
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        return system_msgs + other_msgs

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def _record_llm_call(
        self,
        state: SessionState,
        *,
        iteration: int,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        latency_ms: float,
        ttft_ms: float,
        finish_reason: str,
        tool_calls: int,
        error: str,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self._trace.record_call(
            session_id=state.session_id,
            model=self._main_model.alias,
            iteration=iteration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            error=error,
        )
        # Calibrate token estimator with actual usage
        if messages and prompt_tokens > 0:
            from pc_assistant.model_adapter.content import text_content
            prompt_text = "\n".join(text_content(m.get("content", "")) for m in messages)
            self._token_estimator.calibrate(prompt_tokens, prompt_text)

    def _record_turn(
        self,
        state: SessionState,
        user_input: str,
        *,
        outcome: str,
        evidence_required: bool,
        evidence_satisfied: bool,
        elapsed_ms: float,
        turn_base_iterations: int = 0,
        turn_base_prompt_tokens: int = 0,
        turn_base_completion_tokens: int = 0,
    ) -> None:
        self._turn_recorder.record_turn(
            session_id=state.session_id,
            user_input=user_input,
            outcome=outcome,
            iterations=state.total_iterations - turn_base_iterations,
            tool_calls=len(state.tool_call_history),
            prompt_tokens=state.total_prompt_tokens - turn_base_prompt_tokens,
            completion_tokens=state.total_completion_tokens - turn_base_completion_tokens,
            elapsed_ms=elapsed_ms,
            evidence_required=evidence_required,
            evidence_satisfied=evidence_satisfied,
        )

    async def _compaction_llm_call(self, messages: list[dict[str, Any]]) -> str:
        resp: LLMResponse = await self._llm.chat(messages, tools=None, max_tokens=512, cache_control=None)
        if resp.finish_reason == "error" or resp.content.startswith("LLM request failed"):
            raise RuntimeError(resp.content)
        return resp.content

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def run(
        self,
        user_input: str,
        *,
        session_id: str = "",
        confirm_callback: ConfirmFn | None = None,
        attachments: list[ImageAttachment] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        if not self._limiter.is_allowed("agent"):
            yield AgentEvent(
                type="error",
                content="Rate limit exceeded. Please wait before sending another message.",
            )
            return

        state = self._get_state(session_id)
        scope_token = set_memory_scope(derive_memory_scope(state.session_id))
        try:
            async with state.run_lock:
                async for event in self._run_serialized(
                    state,
                    user_input,
                    session_id=session_id,
                    confirm_callback=confirm_callback,
                    attachments=attachments,
                ):
                    yield event
        finally:
            reset_memory_scope(scope_token)

    async def _run_serialized(
        self,
        state: SessionState,
        user_input: str,
        *,
        session_id: str,
        confirm_callback: ConfirmFn | None,
        attachments: list[ImageAttachment] | None,
    ) -> AsyncGenerator[AgentEvent, None]:
        confirm_fn = confirm_callback or self._confirm_callback
        state.cancelled = False
        state.tool_call_history.clear()
        state.last_outcome = "running"
        if self._config.auto_compact_enabled:
            state.conversation.compact_completed_tool_results()
        state.mark_snapshot()

        attachment_blocks = self._resolve_attachments(state.session_id, attachments)
        if attachments and not attachment_blocks:
            yield AgentEvent(
                type="error",
                content="Could not load the attached image(s). Make sure the paths exist and Pillow is installed.",
            )
            state.status = "ready"
            return
        if attachment_blocks and not self._llm.supports_vision and self._vision_broker is None:
            yield AgentEvent(
                type="error",
                content=(
                    "The active LLM provider does not support vision and the dedicated "
                    "vision service is disabled. Enable vision_enabled or use a multimodal model."
                ),
            )
            state.status = "ready"
            return

        turn_start = time.monotonic()
        turn_base_iterations = state.total_iterations
        turn_base_prompt_tokens = state.total_prompt_tokens
        turn_base_completion_tokens = state.total_completion_tokens
        evidence_required = self._evidence.requires_evidence(user_input)
        vision_required = bool(attachment_blocks and not self._llm.supports_vision)
        successful_evidence_results = 0
        try:
            async for event in self._run_loop(
                state,
                user_input,
                evidence_required=evidence_required,
                vision_required=vision_required,
                confirm_fn=confirm_fn,
                attachment_blocks=attachment_blocks,
            ):
                if (
                    event.type == "tool_result"
                    and self._evidence.successful_tool_result(event.tool_result)
                ):
                    successful_evidence_results += 1
                yield event
        finally:
            outcome = state.last_outcome
            if outcome in ("cancelled", "error"):
                state.rollback_if_needed()
            else:
                state.snapshot_len = -1
                # Long-term memory is explicit. Ordinary conversation text and
                # tool use must not silently become durable user memory.
            state.conversation.compact_completed_tool_results()
            self._persist_session_transcript(state)
            self._record_turn(
                state,
                user_input,
                outcome=outcome,
                evidence_required=evidence_required,
                evidence_satisfied=self._evidence.satisfied(successful_evidence_results),
                elapsed_ms=(time.monotonic() - turn_start) * 1000,
                turn_base_iterations=turn_base_iterations,
                turn_base_prompt_tokens=turn_base_prompt_tokens,
                turn_base_completion_tokens=turn_base_completion_tokens,
            )

    async def _run_loop(
        self,
        state: SessionState,
        user_input: str,
        *,
        evidence_required: bool,
        vision_required: bool = False,
        confirm_fn: ConfirmFn | None = None,
        attachment_blocks: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        conv = state.conversation

        plan: StructuredPlan | None = None
        if AgentPlanner.should_plan(user_input):
            plan = await self._planner.plan(
                user_input,
                available_tools=self._registry.list_tools(),
            )
            if plan is not None:
                yield AgentEvent(type="plan", content=plan.to_prompt())
                enriched = f"{user_input}\n\n{plan.to_prompt()}\n\nExecute the plan step by step."
                conv.add_user_with_blocks(enriched, attachment_blocks)
            else:
                conv.add_user_with_blocks(user_input, attachment_blocks)
        else:
            conv.add_user_with_blocks(user_input, attachment_blocks)

        if isinstance(self._memory, ScopedUserMemory):
            user_memory_context = self._memory.build_context_string(query=user_input)
        else:
            # Preserve the injected legacy UserMemory seam while the default
            # implementation uses query-aware, principal-scoped retrieval.
            user_memory_context = self._memory.build_context_string()
        memory_parts = [user_memory_context]
        procedural_ctx = self._procedural_memory.build_context_string()
        if procedural_ctx:
            memory_parts.append(procedural_ctx)
        memory_context = "\n\n".join(p for p in memory_parts if p)
        conv.set_system_context(self._system_prompt)

        # Persist a deterministic summary before the request becomes too large.
        # This is the default path; LLM rewriting remains an explicit opt-in.
        effective_budget = self._config.effective_context_window_budget()
        if (
            self._config.auto_compact_enabled
            and effective_budget > 0
            and conv.estimate_token_count() >= effective_budget * self._config.auto_compact_threshold
        ):
            conv.compress(keep_recent=4)

        if self._config.llm_compact_enabled:
            async def _hydrate_for_compaction(messages: list[dict[str, Any]]) -> str:
                prepared = self._prepare_model_messages(state.session_id, messages)
                return await self._compaction_llm_call(prepared)

            compacted = await compact_conversation_llm(
                conv.get_messages_for_llm_raw(),
                keep_recent=2,
                llm_call=_hydrate_for_compaction,
            )
            if compacted is not None:
                conv.rebuild_from_messages(compacted)

        import uuid as _uuid
        run_id = _uuid.uuid4().hex[:16]
        step_counter = 0

        empty_response_count = 0
        max_empty_retries = 1
        total_tool_calls = 0
        max_total_tool_calls = self._config.max_total_tool_calls
        consecutive_tool_without_answer = 0
        max_consecutive_tool_calls = self._config.max_consecutive_tool_calls
        turn_tool_calls = 0
        successful_tool_results = 0
        vision_observation_calls = 0

        system_prompt = self._system_prompt
        turn_instructions: list[str] = []
        if evidence_required:
            turn_instructions.append(self._evidence.build_instruction())
        if vision_required:
            turn_instructions.append(
                "## Image evidence requirement\n"
                "The current turn contains available image manifests, but you cannot see their pixels. "
                "Call image_inspect with the manifested image_id and a question that you derive from the "
                "user's current request. The question is required and must not be generic or hard-coded. "
                "Normally make one comprehensive observation call; call again only when a distinct visible "
                "detail is necessary. You remain responsible for diagnosis, recommendations, and solutions "
                "after receiving the observation."
            )
        turn_context = "\n\n".join(turn_instructions)

        for iteration in range(self._config.max_iterations):
            if state.cancelled:
                state.last_outcome = "cancelled"
                yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                state.status = "ready"
                return

            state.status = "thinking"
            state.total_iterations += 1

            # Tool schemas are sent outside the message list, but still consume
            # the provider context window. Keep the static core schemas in the
            # request (and therefore cacheable) while reserving room for them
            # and for the requested completion before trimming history.
            tools = self._registry.all_schemas() if len(self._registry) > 0 else None
            schema_tokens = 0
            if tools:
                schema_tokens = self._token_estimator.text_tokens(
                    json.dumps(tools, ensure_ascii=False, sort_keys=True),
                )
            message_budget, request_max_tokens = allocate_context_budget(
                self._config.effective_context_window_budget(),
                schema_tokens,
                self._config.max_tokens,
            )
            if message_budget <= 0 or request_max_tokens <= 0:
                state.last_outcome = "error"
                state.status = "ready"
                yield AgentEvent(
                    type="error",
                    content=(
                        "Configured context window is too small for the static tool schemas. "
                        "Increase the active model's context_window or reduce the tool surface."
                    ),
                    iteration=iteration,
                )
                return

            raw_messages = conv.get_messages_for_llm_raw()
            messages = assemble_llm_messages(
                system_prompt,
                raw_messages,
                user_input,
                working_directory=self._config.working_directory,
                memory_context=memory_context,
                turn_context=turn_context,
            )
            messages = truncate_messages(
                messages,
                budget=message_budget,
            )
            messages = self._ensure_system_first(messages)
            messages = self._prepare_model_messages(state.session_id, messages)
            if self._token_estimator.messages_tokens(messages) > message_budget:
                state.last_outcome = "error"
                state.status = "ready"
                yield AgentEvent(
                    type="error",
                    content=(
                        "The current turn is larger than the model's input budget after "
                        "deterministic history trimming. Please shorten the request or use /compact."
                    ),
                    iteration=iteration,
                )
                return

            full_content = ""
            emitted_clean_len = 0
            emitted_think_len = 0
            tool_calls_from_stream: list[dict[str, Any]] = []
            accumulated_tool_calls: dict[int, dict[str, Any]] = {}
            finish_reason = ""
            stream_had_error = False
            suppress_unobserved_visual_answer = vision_required and vision_observation_calls == 0

            yield AgentEvent(type="stream_start", iteration=iteration)

            llm_start = time.monotonic()
            llm_ttft: float | None = None
            call_usage: dict[str, Any] = {}
            call_error = ""

            try:
                async for chunk in self._llm.chat_stream(
                    messages,
                    tools=tools,
                    temperature=self._config.llm_temperature,
                    max_tokens=request_max_tokens,
                    cancel_key=state.session_id or None,
                    cache_control=self._cache_plan.cache_control_hint() if self._cache_plan.supports_caching else None,
                ):
                    if llm_ttft is None:
                        llm_ttft = (time.monotonic() - llm_start) * 1000

                    if state.cancelled:
                        state.last_outcome = "cancelled"
                        yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                        state.status = "ready"
                        return

                    if chunk.finish_reason == "error":
                        yield AgentEvent(
                            type="error",
                            content=chunk.delta_content,
                            iteration=iteration,
                        )
                        stream_had_error = True
                        break

                    if chunk.delta_content:
                        full_content += chunk.delta_content
                        clean_part, think_part = _strip_think_tags(full_content)

                        new_content = clean_part[emitted_clean_len:]
                        if new_content and not suppress_unobserved_visual_answer:
                            yield AgentEvent(type="stream_delta", content=new_content, iteration=iteration)
                            emitted_clean_len = len(clean_part)

                        new_think = think_part[emitted_think_len:]
                        if new_think:
                            yield AgentEvent(type="stream_think_delta", content=new_think, iteration=iteration)
                            emitted_think_len = len(think_part)

                    if chunk.delta_thinking:
                        yield AgentEvent(type="stream_think_delta", content=chunk.delta_thinking, iteration=iteration)

                    if chunk.delta_tool_calls:
                        for dtc in chunk.delta_tool_calls:
                            idx = dtc.get("index", len(accumulated_tool_calls))
                            func_delta = dtc.get("function", {})
                            delta_args = func_delta.get("arguments", "")

                            if isinstance(delta_args, dict):
                                accumulated_tool_calls[idx] = {
                                    "id": dtc.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": func_delta.get("name", ""),
                                        "arguments": delta_args,
                                    },
                                }
                                continue

                            if idx not in accumulated_tool_calls:
                                accumulated_tool_calls[idx] = {
                                    "id": dtc.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": "",
                                        "arguments": "",
                                    },
                                }
                            acc = accumulated_tool_calls[idx]
                            if dtc.get("id"):
                                acc["id"] = dtc["id"]
                            if func_delta.get("name"):
                                acc["function"]["name"] += func_delta["name"]
                            if delta_args:
                                if isinstance(acc["function"]["arguments"], str):
                                    acc["function"]["arguments"] += delta_args
                                else:
                                    acc["function"]["arguments"] = delta_args

                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason

                    if chunk.usage:
                        call_usage = {**call_usage, **chunk.usage}
                        prompt_tokens = chunk.usage.get("prompt_tokens") or chunk.usage.get("input_tokens") or 0
                        completion_tokens = chunk.usage.get("completion_tokens") or chunk.usage.get("output_tokens") or 0
                        state.total_prompt_tokens += int(prompt_tokens)
                        state.total_completion_tokens += int(completion_tokens)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                call_error = str(e)
                error_msg = str(e)
                if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                    error_msg = (
                        f"LLM request timed out after {self._config.llm_timeout}s. "
                        "Possible causes: prompt too long, model is busy, or server overloaded. "
                        "You can increase PC_LLM_TIMEOUT in config if needed."
                    )
                yield AgentEvent(
                    type="error",
                    content=f"LLM stream error: {error_msg}",
                    iteration=iteration,
                )
                stream_had_error = True

            self._record_llm_call(
                state,
                iteration=iteration,
                prompt_tokens=call_usage.get("prompt_tokens") or call_usage.get("input_tokens") or 0,
                completion_tokens=call_usage.get("completion_tokens") or call_usage.get("output_tokens") or 0,
                cached_tokens=(
                    call_usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                    or call_usage.get("input_tokens_details", {}).get("cached_tokens", 0)
                    or 0
                ),
                latency_ms=(time.monotonic() - llm_start) * 1000,
                ttft_ms=llm_ttft or 0.0,
                finish_reason=finish_reason,
                tool_calls=len(accumulated_tool_calls),
                error=call_error,
                messages=messages,
            )

            if stream_had_error:
                state.last_outcome = "error"
                state.status = "ready"
                return

            self._connected = True

            if accumulated_tool_calls:
                final_tool_calls = []
                for idx in sorted(accumulated_tool_calls.keys()):
                    tc = accumulated_tool_calls[idx]
                    if "function" in tc and isinstance(tc["function"].get("arguments"), str):
                        try:
                            tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                        except (json.JSONDecodeError, TypeError):
                            tc["function"]["arguments"] = {}
                    final_tool_calls.append(tc)
                tool_calls_from_stream = final_tool_calls

            clean_content = _strip_think_tags(full_content)[0]

            yield AgentEvent(type="stream_end", iteration=iteration)

            if tool_calls_from_stream:
                stored_content = clean_content.strip()
                conv.add_assistant(stored_content, delta_tool_calls=tool_calls_from_stream)
                for tool_call in tool_calls_from_stream:
                    if state.cancelled:
                        state.last_outcome = "cancelled"
                        yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                        state.status = "ready"
                        return

                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "")
                    arguments = func.get("arguments", {})
                    tool_call_id = tool_call.get("id", "")

                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except (json.JSONDecodeError, TypeError):
                            arguments = {}

                    if not isinstance(arguments, dict):
                        arguments = {}

                    is_loop, loop_reason = self._check_tool_loop(tool_name, arguments, state.tool_call_history)
                    if is_loop:
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result={"error": f"Loop detected: {loop_reason}"},
                            content=f"Stopped: {loop_reason}",
                            iteration=iteration,
                        )
                        conv.add_tool_result(tool_call_id, f"Stopped: {loop_reason}", tool_name=tool_name)
                        state.tool_call_history.clear()
                        break

                    verdict, prepared_call = await self._executor.authorize(
                        tool_name,
                        arguments,
                        confirm_callback=confirm_fn,
                    )

                    if verdict.rejected:
                        denial = verdict.code == RefusalCode.CONFIRMATION_DENIED
                        yield AgentEvent(
                            type="tool_call",
                            tool_name=tool_name,
                            tool_args=arguments,
                            blocked=True,
                            content=(
                                f"User denied: {tool_name}"
                                if denial
                                else verdict.reason
                            ),
                            iteration=iteration,
                        )
                        conv.add_tool_result(
                            tool_call_id,
                            verdict.to_structured_message(),
                            tool_name=tool_name,
                        )
                        continue

                    if prepared_call is None:
                        raise RuntimeError("Verifier accepted without preparing a tool capability")

                    yield AgentEvent(
                        type="tool_call",
                        tool_name=tool_name,
                        tool_args=arguments,
                        iteration=iteration,
                    )

                    total_tool_calls += 1
                    consecutive_tool_without_answer += 1
                    turn_tool_calls += 1
                    step_counter += 1

                    tool_obj = self._registry.get(tool_name)
                    idem_key = ""
                    if tool_obj is not None and tool_obj.is_side_effecting:
                        idem_key = IdempotencyLog.make_key(run_id, step_counter, tool_name, arguments)
                        from pc_assistant.harness.idempotency import _SENTINEL
                        cached = self._idempotency.check(idem_key)
                        if cached is not _SENTINEL:
                            result_str = str(cached)
                            result_str = self._smart_truncate(result_str, tool_name, cached)
                            inline_blocks = self._inline_image_blocks(state.session_id, tool_name, cached)
                            if inline_blocks is not None:
                                conv.add_tool_result_blocks(tool_call_id, inline_blocks, tool_name=tool_name)
                                if not self._llm.supports_vision:
                                    vision_required = True
                            else:
                                conv.add_tool_result(tool_call_id, f"[idempotent-replay] {result_str}", tool_name=tool_name)
                            yield AgentEvent(
                                type="tool_result",
                                tool_name=tool_name,
                                tool_args=arguments,
                                tool_result=self._bounded_event_result(cached, result_str),
                                content=result_str,
                                iteration=iteration,
                            )
                            if self._evidence.successful_tool_result(cached):
                                successful_tool_results += 1
                            cached_artifact = (
                                cached.get("artifact")
                                if isinstance(cached, dict)
                                else None
                            )
                            if (
                                isinstance(cached_artifact, dict)
                                and cached_artifact.get("visibility") == "user"
                                and cached_artifact.get("artifact_id")
                            ):
                                yield AgentEvent(
                                    type="artifact",
                                    tool_name=tool_name,
                                    artifact=cached_artifact,
                                    iteration=iteration,
                                )
                            continue

                    if total_tool_calls >= max_total_tool_calls:
                        state.last_outcome = "limit"
                        yield AgentEvent(
                            type="iteration_limit",
                            content=f"Total tool call limit reached ({max_total_tool_calls}).",
                            iteration=iteration,
                        )
                        state.status = "ready"
                        return

                    if consecutive_tool_without_answer >= max_consecutive_tool_calls:
                        state.last_outcome = "limit"
                        yield AgentEvent(
                            type="iteration_limit",
                            content=f"Too many tool calls ({consecutive_tool_without_answer}) without producing an answer.",
                            iteration=iteration,
                        )
                        state.status = "ready"
                        return

                    state.status = f"executing_{tool_name}"

                    try:
                        execute_coro = self._executor.commit(prepared_call)
                        state.tool_task = asyncio.create_task(execute_coro)
                        try:
                            result = await state.tool_task
                        except asyncio.CancelledError:
                            if state.cancelled:
                                state.status = "ready"
                                state.last_outcome = "cancelled"
                                yield AgentEvent(type="cancelled", content="Operation cancelled by user.", iteration=iteration)
                                return
                            raise
                        finally:
                            state.tool_task = None
                        safe_result = self._event_safe_result(result)
                        if (
                            tool_name == "image_inspect"
                            and isinstance(safe_result, dict)
                            and safe_result.get("observation_id")
                            and not safe_result.get("error")
                        ):
                            vision_observation_calls += 1
                        result_str = str(safe_result)
                        result_str = self._smart_truncate(result_str, tool_name, result)
                        inline_blocks = self._inline_image_blocks(state.session_id, tool_name, result)
                        if idem_key and not self._contains_binary_image(result):
                            self._idempotency.record(idem_key, result)
                        if inline_blocks is not None:
                            conv.add_tool_result_blocks(tool_call_id, inline_blocks, tool_name=tool_name)
                            if not self._llm.supports_vision:
                                vision_required = True
                            result_str = self._inline_image_note(result)
                        else:
                            conv.add_tool_result(tool_call_id, result_str, tool_name=tool_name)
                        state.status = "thinking"
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result=self._bounded_event_result(result, result_str),
                            content=result_str,
                            iteration=iteration,
                        )
                        if self._evidence.successful_tool_result(result):
                            successful_tool_results += 1
                        artifact = (
                            safe_result.get("artifact")
                            if isinstance(safe_result, dict)
                            else None
                        )
                        if (
                            isinstance(artifact, dict)
                            and artifact.get("visibility") == "user"
                            and artifact.get("artifact_id")
                        ):
                            yield AgentEvent(
                                type="artifact",
                                tool_name=tool_name,
                                artifact=artifact,
                                iteration=iteration,
                            )
                    except asyncio.CancelledError:
                        state.status = "ready"
                        state.last_outcome = "cancelled"
                        return
                    except Exception as e:
                        error_msg = f"Error: {e}"
                        conv.add_tool_result(tool_call_id, error_msg, tool_name=tool_name)
                        state.status = "thinking"
                        yield AgentEvent(
                            type="tool_result",
                            tool_name=tool_name,
                            tool_args=arguments,
                            tool_result={"error": str(e)},
                            content=error_msg,
                            iteration=iteration,
                        )
            else:
                consecutive_tool_without_answer = 0

                if vision_required and vision_observation_calls == 0:
                    conv.add_user(
                        "[System] Your draft relied on an image without visual evidence and was not delivered. "
                        "Call image_inspect for the available image_id now and supply a visual question derived "
                        "from the user's request. Ask only for visible observations; "
                        "perform diagnosis or solution reasoning yourself after the tool result."
                    )
                    continue

                if finish_reason == "length" and clean_content:
                    if evidence_required and successful_tool_results == 0:
                        yield AgentEvent(
                            type="evidence_warning",
                            content="Final answer not verified against tool results.",
                            iteration=iteration,
                        )
                    conv.add_assistant_final(clean_content)
                    yield AgentEvent(
                        type="final_answer",
                        content=clean_content,
                        iteration=iteration,
                    )
                    state.last_outcome = "answer"
                    state.status = "ready"
                    return

                if not clean_content:
                    empty_response_count += 1
                    if empty_response_count > max_empty_retries:
                        conv.add_assistant_final("I was unable to generate a response. Please try again.")
                        yield AgentEvent(
                            type="final_answer",
                            content="I was unable to generate a response. Please try again.",
                            iteration=iteration,
                        )
                        state.last_outcome = "answer"
                        state.status = "ready"
                        return
                    conv.add_user("[System] You did not produce any output. Please respond to the user's question directly.")
                    continue

                empty_response_count = 0

                if (
                    self._reflection is not None
                    and iteration < self._config.max_iterations - 1
                    and self._reflection.should_reflect(user_input, clean_content, turn_tool_calls)
                ):
                    try:
                        passes, critique = await self._reflection.check(user_input, clean_content)
                        if not passes and critique:
                            conv.add_assistant(clean_content)
                            conv.add_user(f"[System reflection] Your answer may be incomplete: {critique}. Please improve it.")
                            continue
                    except Exception:
                        pass

                if evidence_required and successful_tool_results == 0:
                    yield AgentEvent(
                        type="evidence_warning",
                        content="Final answer not verified against tool results.",
                        iteration=iteration,
                    )
                conv.add_assistant_final(clean_content)
                yield AgentEvent(
                    type="final_answer",
                    content=clean_content,
                    iteration=iteration,
                )
                state.last_outcome = "answer"
                state.status = "ready"
                return

        state.last_outcome = "limit"
        yield AgentEvent(
            type="iteration_limit",
            content="Maximum iterations reached without a final answer.",
            iteration=self._config.max_iterations,
        )
        state.status = "ready"

    def _prepare_model_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep image bytes request-local and away from text-only main models."""
        if self._llm.supports_vision:
            return self._artifact_store.hydrate_messages(session_id, messages)
        return self._artifact_store.manifest_messages(session_id, messages)

    async def run_simple(self, user_input: str) -> str:
        final_answer = ""
        async for event in self.run(user_input):
            if event.type == "final_answer":
                final_answer = event.content
            elif event.type == "error":
                final_answer = event.content
            elif event.type == "iteration_limit":
                final_answer = event.content
            elif event.type == "cancelled":
                final_answer = event.content
        return final_answer

    def reset_conversation(self) -> None:
        self._default_state.conversation.clear()
        self._session_transcripts.delete("")
        self._artifact_store.cleanup_session("")
        self._system_prompt = build_system_prompt(
            working_directory=self._config.working_directory,
        )
        self._default_state.conversation.set_system_context(self._system_prompt)
