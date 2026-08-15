"""Typed Agent definitions and invocation policy resolution."""

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
RuntimeImplementation = Literal["native", "codex"]
NativeCapability = Literal[
    "workspace_read",
    "workspace_write",
    "command_execution",
    "native_file_edit",
]


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


class AgentDefinitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class ModelBindingSpec(AgentDefinitionModel):
    ownership: Literal["platform", "runtime"]
    model: str = ""
    hint: str = ""

    @model_validator(mode="after")
    def validate_binding(self) -> "ModelBindingSpec":
        if self.ownership == "platform" and not self.model.strip():
            raise ValueError("Platform-managed model binding requires a model alias")
        if self.ownership == "runtime" and self.model:
            raise ValueError(
                "Runtime-managed model binding cannot set a platform model"
            )
        return self


class RuntimeSpec(AgentDefinitionModel):
    implementation: RuntimeImplementation
    model_binding: ModelBindingSpec
    max_concurrency: int = Field(default=1, ge=1, le=32)
    command: tuple[str, ...] = ()
    home: str = ""
    cwd: str = ""
    sandbox: Literal["read-only", "workspace-write"] = "read-only"
    approval_policy: Literal["untrusted", "on-request", "never"] = "never"
    native_capabilities: frozenset[NativeCapability] = frozenset()
    instruction_authority: Literal["required", "supported", "none"] = "supported"
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    max_line_bytes: int = Field(default=4 * 1024 * 1024, ge=4096)
    max_event_queue: int = Field(default=1024, ge=16, le=16_384)

    @model_validator(mode="after")
    def validate_runtime(self) -> "RuntimeSpec":
        if self.implementation == "codex" and not self.command:
            raise ValueError("Codex RuntimeSpec requires a command")
        if (
            self.implementation == "native"
            and self.model_binding.ownership != "platform"
        ):
            raise ValueError("Native RuntimeSpec requires a platform-managed model")
        if self.implementation == "codex" and self.model_binding.ownership != "runtime":
            raise ValueError("Codex RuntimeSpec requires a runtime-managed model")
        return self


class RuntimeProfileLimits(AgentDefinitionModel):
    max_iterations: int | None = Field(default=None, ge=1, le=128)
    max_output_tokens: int | None = Field(default=None, ge=64, le=131_072)


class DelegationPolicy(AgentDefinitionModel):
    allowed: bool = False
    targets: frozenset[AgentId] = frozenset()
    max_depth: int = Field(default=0, ge=0, le=8)
    max_children: int = Field(default=0, ge=0, le=32)
    max_parallel_children: int = Field(default=0, ge=0, le=32)
    max_deadline_seconds: float = Field(default=0.0, ge=0.0, le=86_400.0)

    @model_validator(mode="after")
    def validate_delegation(self) -> "DelegationPolicy":
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


class AgentProfile(AgentDefinitionModel):
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    instructions: Annotated[str, StringConstraints(max_length=200_000)] = ""
    instructions_ref: Annotated[str, StringConstraints(max_length=4096)] = ""
    instructions_required: bool = True
    default_skills: frozenset[ResourceId] = frozenset()
    allowed_platform_tools: frozenset[str] = frozenset()
    platform_capability_ceiling: frozenset[str] = frozenset()
    runtime_native_capability_ceiling: frozenset[NativeCapability] = frozenset()
    runtime_limits: RuntimeProfileLimits = Field(default_factory=RuntimeProfileLimits)
    delegation: DelegationPolicy = Field(default_factory=DelegationPolicy)
    visibility: Visibility = "delegate"
    callable_by: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_profile(self) -> "AgentProfile":
        if bool(self.instructions) == bool(self.instructions_ref):
            raise ValueError("Profile requires exactly one instructions source")
        if self.visibility == "system" and not self.callable_by:
            raise ValueError("System Profile requires an explicit caller allowlist")
        return self


class AgentDefinition(AgentDefinitionModel):
    runtime_spec_id: ResourceId
    profile_id: ResourceId
    enabled: bool = True


class InvocationLimits(AgentDefinitionModel):
    deadline_seconds: float = Field(default=120.0, gt=0.0, le=86_400.0)
    max_gateway_tool_calls: int = Field(default=50, ge=0, le=10_000)
    max_artifact_bytes: int = Field(default=32 * 1024 * 1024, ge=0)
    max_children: int = Field(default=0, ge=0, le=32)
    max_parallel_children: int = Field(default=0, ge=0, le=32)


class ResolvedInvocationPolicy(AgentDefinitionModel):
    agent_id: AgentId
    agent_definition_digest: str
    runtime_spec_digest: str
    profile_digest: str
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


class AgentSystemConfig(AgentDefinitionModel):
    runtime_specs: dict[ResourceId, RuntimeSpec]
    profiles: dict[ResourceId, AgentProfile]
    agents: dict[AgentId, AgentDefinition]
    default_agent: AgentId

    @model_validator(mode="after")
    def validate_references(self) -> "AgentSystemConfig":
        if (
            self.default_agent not in self.agents
            or not self.agents[self.default_agent].enabled
        ):
            raise ValueError("default_agent must reference an enabled Agent")
        for agent_id, definition in self.agents.items():
            runtime = self.runtime_specs.get(definition.runtime_spec_id)
            profile = self.profiles.get(definition.profile_id)
            if runtime is None:
                raise ValueError(
                    f"Agent '{agent_id}' references an unknown RuntimeSpec"
                )
            if profile is None:
                raise ValueError(f"Agent '{agent_id}' references an unknown Profile")
            if (
                profile.instructions_required
                and runtime.instruction_authority == "none"
            ):
                raise ValueError(
                    f"Agent '{agent_id}' requires authoritative Profile instructions"
                )
            if (
                not profile.runtime_native_capability_ceiling
                <= runtime.native_capabilities
            ):
                raise ValueError(
                    f"Agent '{agent_id}' Profile requests unsupported native capabilities"
                )
            unknown_targets = profile.delegation.targets - self.agents.keys()
            if unknown_targets:
                raise ValueError(
                    f"Agent '{agent_id}' delegation references unknown targets"
                )
        return self


class AgentNotCallableError(PermissionError):
    pass


class AgentDefinitionResolver:
    """Resolve one immutable Agent definition and invocation authorization snapshot."""

    def __init__(
        self, config: AgentSystemConfig, *, config_revision_id: str = ""
    ) -> None:
        self._config = config
        self._config_revision_id = config_revision_id

    @property
    def config(self) -> AgentSystemConfig:
        return self._config

    def definition_digest(self, agent_id: str) -> str:
        definition, runtime, profile = self._parts(agent_id)
        payload = {
            "agent_id": agent_id,
            "definition": definition.model_dump(mode="json"),
            "runtime": runtime.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def resolve_agent_id(
        self,
        requested: str | None,
        *,
        invocation_kind: InvocationKind,
        caller_id: str,
    ) -> str:
        agent_id = (requested or self._config.default_agent).strip()
        definition, _, profile = self._parts(agent_id)
        if not definition.enabled:
            raise AgentNotCallableError(f"Agent '{agent_id}' is disabled")
        allowed = (
            (invocation_kind == "user" and profile.visibility == "user")
            or (invocation_kind == "delegate" and profile.visibility == "delegate")
            or (invocation_kind == "system" and profile.visibility == "system")
        )
        if not allowed:
            raise AgentNotCallableError(
                f"Agent '{agent_id}' is not callable as {invocation_kind}"
            )
        if profile.callable_by and caller_id not in profile.callable_by:
            raise AgentNotCallableError(f"Caller cannot invoke Agent '{agent_id}'")
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
        _, runtime, profile = self._parts(agent_id)
        capabilities = (
            principal_capabilities
            if "*" in profile.platform_capability_ceiling
            else principal_capabilities & profile.platform_capability_ceiling
        )
        tools = (
            available_tools
            if "*" in profile.allowed_platform_tools
            else available_tools & profile.allowed_platform_tools
        )
        skills = installed_skills & profile.default_skills
        native = runtime.native_capabilities & profile.runtime_native_capability_ceiling
        if requested_capabilities is not None:
            capabilities &= requested_capabilities
        if requested_tools is not None:
            tools &= requested_tools
        if requested_skills is not None:
            skills &= requested_skills
        if requested_native_capabilities is not None:
            native &= requested_native_capabilities
        if parent is not None:
            capabilities &= parent.platform_capabilities
            tools &= parent.allowed_platform_tools
            skills &= parent.allowed_skills
            native &= _delegable_native_capabilities(parent.platform_capabilities)
            artifact_ids &= parent.artifact_ids
        resolved_limits = limits or InvocationLimits(
            max_children=profile.delegation.max_children,
            max_parallel_children=profile.delegation.max_parallel_children,
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
        delegation_max_depth = profile.delegation.max_depth
        delegation_max_deadline_seconds = profile.delegation.max_deadline_seconds
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
            agent_definition_digest=self.definition_digest(agent_id),
            runtime_spec_digest=runtime.digest(),
            profile_digest=profile.digest(),
            invocation_kind=invocation_kind,
            caller_id=caller_id,
            platform_capabilities=frozenset(capabilities),
            allowed_platform_tools=frozenset(tools),
            allowed_skills=frozenset(skills),
            runtime_native_capabilities=frozenset(native),
            delegation_targets=profile.delegation.targets,
            delegation_max_depth=delegation_max_depth,
            delegation_max_deadline_seconds=delegation_max_deadline_seconds,
            artifact_ids=artifact_ids,
            limits=resolved_limits,
            config_revision_id=self._config_revision_id,
        )

    def runtime_spec(self, agent_id: str) -> RuntimeSpec:
        return self._parts(agent_id)[1]

    def profile(self, agent_id: str) -> AgentProfile:
        return self._parts(agent_id)[2]

    def _parts(
        self, agent_id: str
    ) -> tuple[AgentDefinition, RuntimeSpec, AgentProfile]:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", agent_id):
            raise LookupError("Invalid Agent ID")
        definition = self._config.agents.get(agent_id)
        if definition is None:
            raise LookupError(f"Unknown Agent '{agent_id}'")
        return (
            definition,
            self._config.runtime_specs[definition.runtime_spec_id],
            self._config.profiles[definition.profile_id],
        )
