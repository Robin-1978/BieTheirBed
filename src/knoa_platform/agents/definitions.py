"""Node-owned Agent configuration and invocation policy resolution."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

AgentId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    ),
]
ResourceId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$",
    ),
]
InvocationKind = Literal["user", "delegate", "system"]
Visibility = Literal["user", "delegate", "system"]
AgentKind = Literal["knoa", "codex"]
NativeCapability = Literal[
    "workspace_read",
    "workspace_write",
    "command_execution",
    "native_file_edit",
]
CODEX_READ_ONLY_CAPABILITIES: frozenset[NativeCapability] = frozenset(
    {"workspace_read", "command_execution"}
)
CODEX_WORKSPACE_WRITE_CAPABILITIES: frozenset[NativeCapability] = frozenset(
    {
        "workspace_read",
        "workspace_write",
        "command_execution",
        "native_file_edit",
    }
)


def _delegable_native_capabilities(
    platform_capabilities: frozenset[str],
) -> frozenset[NativeCapability]:
    delegated: set[NativeCapability] = set()
    if "host_read" in platform_capabilities:
        delegated.add("workspace_read")
    if "host_write" in platform_capabilities:
        delegated.update(("workspace_write", "native_file_edit"))
    if "shell" in platform_capabilities:
        delegated.add("command_execution")
    return frozenset(delegated)


def _canonical_value(value):
    if isinstance(value, dict):
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_canonical_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


class AgentConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def digest(self) -> str:
        payload = json.dumps(
            _canonical_value(self.model_dump(mode="python")),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class ModelBindingSpec(AgentConfigModel):
    ownership: Literal["platform", "runtime"]
    model: str = ""
    hint: str = ""

    @model_validator(mode="after")
    def validate_binding(self) -> ModelBindingSpec:
        if self.ownership == "platform" and not self.model.strip():
            raise ValueError("Platform-managed model binding requires a model alias")
        if self.ownership == "runtime" and self.model:
            raise ValueError(
                "Runtime-managed model binding cannot set a platform model"
            )
        return self


class AgentRuntimeLimits(AgentConfigModel):
    max_iterations: int | None = Field(default=None, ge=1, le=128)
    max_output_tokens: int | None = Field(default=None, ge=64, le=131_072)


class DelegationPolicy(AgentConfigModel):
    allowed: bool = False
    targets: frozenset[AgentId] = frozenset()
    max_depth: int = Field(default=0, ge=0, le=8)
    max_children: int = Field(default=0, ge=0, le=32)
    max_parallel_children: int = Field(default=0, ge=0, le=32)
    max_deadline_seconds: float = Field(default=0.0, ge=0.0, le=86_400.0)

    @model_validator(mode="after")
    def validate_delegation(self) -> DelegationPolicy:
        if not self.allowed and any(
            (
                self.targets,
                self.max_depth,
                self.max_children,
                self.max_parallel_children,
            )
        ):
            raise ValueError(
                "Disabled delegation cannot define targets or child limits"
            )
        if self.allowed and (
            self.max_depth < 1
            or self.max_children < 1
            or self.max_parallel_children < 1
            or self.max_deadline_seconds <= 0
        ):
            raise ValueError("Enabled delegation requires positive bounded limits")
        if self.max_parallel_children > self.max_children:
            raise ValueError("Parallel child limit cannot exceed total child limit")
        return self


class NodeAgent(AgentConfigModel):
    """One complete Agent owned and executed by this Knoa Node."""

    kind: AgentKind
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    instructions: Annotated[str, StringConstraints(max_length=200_000)] = ""
    instructions_ref: Annotated[str, StringConstraints(max_length=4096)] = ""
    instructions_required: bool = True
    visibility: Visibility = "delegate"
    enabled: bool = True

    model_binding: ModelBindingSpec
    max_concurrency: int = Field(default=1, ge=1, le=32)

    default_skill_refs: frozenset[ResourceId] = frozenset()
    allowed_skill_refs: frozenset[ResourceId] = frozenset()
    allowed_platform_tools: frozenset[str] = frozenset()
    platform_capability_ceiling: frozenset[str] = frozenset()
    native_capability_ceiling: frozenset[NativeCapability] = frozenset()
    runtime_limits: AgentRuntimeLimits = Field(default_factory=AgentRuntimeLimits)
    delegation: DelegationPolicy = Field(default_factory=DelegationPolicy)
    callable_by: frozenset[str] = frozenset()

    command: tuple[str, ...] = ()
    home: str = ""
    cwd: str = ""
    sandbox: Literal["read-only", "workspace-write"] = "read-only"
    approval_policy: Literal["untrusted", "on-request", "never"] = "never"
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    max_line_bytes: int = Field(default=4 * 1024 * 1024, ge=4096)
    max_event_queue: int = Field(default=1024, ge=16, le=16_384)

    @model_validator(mode="after")
    def validate_agent(self) -> NodeAgent:
        if bool(self.instructions) == bool(self.instructions_ref):
            raise ValueError("NodeAgent requires exactly one instructions source")
        if not self.default_skill_refs <= self.allowed_skill_refs:
            raise ValueError("Default Skill refs must be allowed by the NodeAgent")
        if self.visibility == "system" and not self.callable_by:
            raise ValueError("System NodeAgent requires a caller allowlist")
        if self.kind == "knoa":
            if self.model_binding.ownership != "platform":
                raise ValueError("Knoa Agent requires a platform-managed model")
            if self.command or self.home or self.cwd:
                raise ValueError("Knoa Agent cannot configure an external command")
            if self.native_capability_ceiling:
                raise ValueError(
                    "Knoa Agent cannot request external runtime capabilities"
                )
        else:
            if not self.command:
                raise ValueError("Codex Agent requires a command")
            if self.model_binding.ownership != "runtime":
                raise ValueError("Codex Agent requires a runtime-managed model")
            expected = (
                CODEX_READ_ONLY_CAPABILITIES
                if self.sandbox == "read-only"
                else CODEX_WORKSPACE_WRITE_CAPABILITIES
            )
            if self.native_capability_ceiling != expected:
                raise ValueError(
                    "Codex Agent capabilities must match its sandbox bundle"
                )
        return self


class InvocationLimits(AgentConfigModel):
    deadline_seconds: float = Field(default=120.0, gt=0.0, le=86_400.0)
    max_gateway_tool_calls: int = Field(default=50, ge=0, le=10_000)
    max_artifact_bytes: int = Field(default=32 * 1024 * 1024, ge=0)
    max_children: int = Field(default=0, ge=0, le=32)
    max_parallel_children: int = Field(default=0, ge=0, le=32)


class ResolvedInvocationPolicy(AgentConfigModel):
    agent_id: AgentId
    node_agent_digest: str
    invocation_kind: InvocationKind
    caller_id: str
    platform_capabilities: frozenset[str]
    allowed_platform_tools: frozenset[str]
    allowed_skills: frozenset[str]
    runtime_native_capabilities: frozenset[NativeCapability]
    delegation_targets: frozenset[AgentId] = frozenset()
    delegation_max_depth: int = Field(default=0, ge=0, le=8)
    delegation_max_deadline_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=86_400.0,
    )
    artifact_ids: frozenset[str] = frozenset()
    limits: InvocationLimits = Field(default_factory=InvocationLimits)
    config_revision_id: str = ""

    @property
    def policy_digest(self) -> str:
        return self.digest()


class NodeAgentCatalog(AgentConfigModel):
    agents: dict[AgentId, NodeAgent]
    default_agent: AgentId

    @model_validator(mode="after")
    def validate_agents(self) -> NodeAgentCatalog:
        if (
            self.default_agent not in self.agents
            or not self.agents[self.default_agent].enabled
        ):
            raise ValueError("default_agent must reference an enabled NodeAgent")
        if self.agents[self.default_agent].visibility != "user":
            raise ValueError("default_agent must reference a user-visible NodeAgent")
        for agent_id, agent in self.agents.items():
            unknown_targets = agent.delegation.targets - self.agents.keys()
            if unknown_targets:
                raise ValueError(
                    f"NodeAgent '{agent_id}' delegation references unknown targets"
                )
            invalid_targets = {
                target
                for target in agent.delegation.targets
                if self.agents[target].visibility != "delegate"
            }
            if invalid_targets:
                raise ValueError(
                    f"NodeAgent '{agent_id}' delegation targets must be delegate-visible"
                )
        return self


class AgentNotCallableError(PermissionError):
    pass


class NodeAgentResolver:
    """Resolve one Node-owned Agent into an immutable invocation snapshot."""

    def __init__(
        self, config: NodeAgentCatalog, *, config_revision_id: str = ""
    ) -> None:
        self._config = config
        self._config_revision_id = config_revision_id

    @property
    def config(self) -> NodeAgentCatalog:
        return self._config

    def agent_digest(self, agent_id: str) -> str:
        return self.agent(agent_id).digest()

    def resolve_agent_id(
        self,
        requested: str | None,
        *,
        invocation_kind: InvocationKind,
        caller_id: str,
    ) -> str:
        agent_id = (requested or self._config.default_agent).strip()
        agent = self.agent(agent_id)
        if not agent.enabled:
            raise AgentNotCallableError(f"NodeAgent '{agent_id}' is disabled")
        if invocation_kind != agent.visibility:
            raise AgentNotCallableError(
                f"NodeAgent '{agent_id}' is not callable as {invocation_kind}"
            )
        if agent.callable_by and caller_id not in agent.callable_by:
            raise AgentNotCallableError(
                f"Caller cannot invoke NodeAgent '{agent_id}'"
            )
        return agent_id

    def resolve_policy(
        self,
        requested: str | None,
        *,
        invocation_kind: InvocationKind,
        caller_id: str,
        principal_capabilities: frozenset[str],
        available_tools: frozenset[str],
        installed_skills: frozenset[str],
        requested_capabilities: frozenset[str] | None = None,
        requested_tools: frozenset[str] | None = None,
        requested_skills: frozenset[str] | None = None,
        requested_native_capabilities: frozenset[NativeCapability] | None = None,
        parent: ResolvedInvocationPolicy | None = None,
        artifact_ids: frozenset[str] = frozenset(),
        limits: InvocationLimits | None = None,
    ) -> ResolvedInvocationPolicy:
        agent_id = self.resolve_agent_id(
            requested,
            invocation_kind=invocation_kind,
            caller_id=caller_id,
        )
        agent = self.agent(agent_id)
        capabilities = (
            principal_capabilities
            if "*" in agent.platform_capability_ceiling
            else principal_capabilities & agent.platform_capability_ceiling
        )
        tools = (
            available_tools
            if "*" in agent.allowed_platform_tools
            else available_tools & agent.allowed_platform_tools
        )
        skills = installed_skills & agent.default_skill_refs
        native = agent.native_capability_ceiling
        if requested_capabilities is not None:
            capabilities &= requested_capabilities
        if requested_tools is not None:
            tools &= requested_tools
        if requested_skills is not None:
            skills = installed_skills & agent.allowed_skill_refs & requested_skills
        if requested_native_capabilities is not None:
            native &= requested_native_capabilities
        if parent is not None:
            capabilities &= parent.platform_capabilities
            tools &= parent.allowed_platform_tools
            skills &= parent.allowed_skills
            native &= _delegable_native_capabilities(parent.platform_capabilities)
            artifact_ids &= parent.artifact_ids
        resolved_limits = limits or InvocationLimits(
            max_children=agent.delegation.max_children,
            max_parallel_children=agent.delegation.max_parallel_children,
        )
        if parent is not None:
            resolved_limits = InvocationLimits(
                deadline_seconds=min(
                    resolved_limits.deadline_seconds,
                    parent.limits.deadline_seconds,
                ),
                max_gateway_tool_calls=min(
                    resolved_limits.max_gateway_tool_calls,
                    parent.limits.max_gateway_tool_calls,
                ),
                max_artifact_bytes=min(
                    resolved_limits.max_artifact_bytes,
                    parent.limits.max_artifact_bytes,
                ),
                max_children=min(
                    resolved_limits.max_children,
                    parent.limits.max_children,
                ),
                max_parallel_children=min(
                    resolved_limits.max_parallel_children,
                    parent.limits.max_parallel_children,
                ),
            )
        if agent.kind == "codex" and native not in {
            CODEX_READ_ONLY_CAPABILITIES,
            CODEX_WORKSPACE_WRITE_CAPABILITIES,
        }:
            raise AgentNotCallableError(
                "Codex invocation requires one supported native capability bundle"
            )
        delegation_max_depth = agent.delegation.max_depth
        delegation_max_deadline_seconds = agent.delegation.max_deadline_seconds
        if parent is not None:
            delegation_max_depth = min(
                delegation_max_depth,
                max(0, parent.delegation_max_depth - 1),
            )
            if parent.delegation_max_deadline_seconds:
                delegation_max_deadline_seconds = min(
                    delegation_max_deadline_seconds,
                    parent.delegation_max_deadline_seconds,
                )
        return ResolvedInvocationPolicy(
            agent_id=agent_id,
            node_agent_digest=self.agent_digest(agent_id),
            invocation_kind=invocation_kind,
            caller_id=caller_id,
            platform_capabilities=frozenset(capabilities),
            allowed_platform_tools=frozenset(tools),
            allowed_skills=frozenset(skills),
            runtime_native_capabilities=frozenset(native),
            delegation_targets=agent.delegation.targets,
            delegation_max_depth=delegation_max_depth,
            delegation_max_deadline_seconds=delegation_max_deadline_seconds,
            artifact_ids=artifact_ids,
            limits=resolved_limits,
            config_revision_id=self._config_revision_id,
        )

    def agent(self, agent_id: str) -> NodeAgent:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", agent_id):
            raise LookupError("Invalid Agent ID")
        try:
            return self._config.agents[agent_id]
        except KeyError as exc:
            raise LookupError(f"Unknown NodeAgent '{agent_id}'") from exc
