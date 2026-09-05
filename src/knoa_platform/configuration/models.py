"""Versioned managed configuration contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from knoa_platform.agents.definitions import NodeAgentCatalog

SafeId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$",
    ),
]


def _canonical_value(value):
    if isinstance(value, dict):
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_canonical_value(item) for item in value),
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManagedProviderConfig(ConfigurationModel):
    driver: Literal[
        "llamacpp",
        "openai",
        "openai_compatible",
        "anthropic",
        "workspace_remote",
    ]
    server_url: str = ""
    api_base: str = ""
    api_key_ref: str = ""
    api_key_env: str = ""
    remote_deployment_id: str = ""
    direct_gateway_url: str = ""
    secret_version: int = Field(default=0, ge=0)
    requires_api_key: bool | None = None
    timeout_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)

    @model_validator(mode="after")
    def validate_secret_source(self) -> ManagedProviderConfig:
        if self.api_key_ref and self.api_key_env:
            raise ValueError("Provider must use one API key source")
        if self.driver in {"openai", "anthropic"} and not (
            self.api_key_ref or self.api_key_env
        ):
            raise ValueError("Provider requires an API key reference")
        if self.driver == "workspace_remote":
            if not self.remote_deployment_id:
                raise ValueError("Workspace remote Provider requires a deployment ID")
            if self.api_key_ref or self.api_key_env:
                raise ValueError("Workspace remote Provider cannot bind an API key")
            if self.server_url or self.api_base:
                raise ValueError("Workspace remote Provider cannot bind a local endpoint")
        elif self.remote_deployment_id or self.direct_gateway_url:
            raise ValueError("Only Workspace remote Provider accepts remote routing fields")
        return self


class ManagedModelConfig(ConfigurationModel):
    provider: SafeId
    model: str
    supports_vision: bool | None = None
    context_window: int | None = Field(default=None, ge=512, le=10_000_000)
    thinking: Literal["enabled", "disabled", "auto"] | None = None


class ManagedModelDeploymentConfig(ConfigurationModel):
    model_alias: SafeId
    resource_id: SafeId
    display_name: str = ""
    enabled: bool = True
    share_enabled: bool = False
    max_remote_concurrency: int = Field(default=1, ge=1, le=64)
    allowed_node_ids: tuple[SafeId, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def validate_allowed_nodes(self) -> ManagedModelDeploymentConfig:
        if len(set(self.allowed_node_ids)) != len(self.allowed_node_ids):
            raise ValueError("Model deployment allowed_node_ids must be unique")
        if not self.share_enabled and self.allowed_node_ids:
            raise ValueError("Disabled Model sharing cannot retain allowed Nodes")
        return self


class ManagedSkillConfig(ConfigurationModel):
    package_id: str = ""
    source: str = ""
    enabled: bool = True
    content_digest: str = ""

    @model_validator(mode="after")
    def validate_content_digest(self) -> ManagedSkillConfig:
        if not self.package_id and not self.source:
            raise ValueError("Skill requires package_id or a trusted builtin source")
        if self.package_id and not self.package_id.startswith("skill-"):
            raise ValueError("Skill package_id must reference a Skill package")
        if self.content_digest and (
            len(self.content_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.content_digest)
        ):
            raise ValueError("Skill content_digest must be a lowercase SHA-256 digest")
        return self


class ManagedMCPToolPolicyConfig(ConfigurationModel):
    effect: Literal[
        "read_only",
        "internal_write",
        "local_write",
        "external_side_effect",
        "desktop_control",
    ]
    capabilities: frozenset[
        Literal[
            "host_read",
            "host_write",
            "shell",
            "network",
            "desktop_observe",
            "desktop_control",
            "memory_read",
            "memory_write",
            "mcp",
            "task_management",
        ]
    ] = frozenset()
    risk: Literal["low", "medium", "high"]


class ManagedMCPConfig(ConfigurationModel):
    transport: Literal["stdio", "streamable_http"]
    package_id: str = ""
    inventory_digest: str = ""
    enabled: bool = True
    command: tuple[str, ...] = ()
    url: str = ""
    working_directory: str = ""
    inherit_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ()
    secret_refs: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    tools: dict[str, ManagedMCPToolPolicyConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport(self) -> ManagedMCPConfig:
        if self.package_id and not self.package_id.startswith("mcp-"):
            raise ValueError("MCP package_id must reference an MCP package")
        if self.transport == "stdio" and not (self.command or self.package_id):
            raise ValueError("stdio MCP requires a package_id or command")
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("HTTP MCP requires a URL")
        if self.package_id and self.transport != "stdio":
            raise ValueError("Only stdio MCP can reference a local package")
        if self.inventory_digest and (
            len(self.inventory_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.inventory_digest)
        ):
            raise ValueError("MCP inventory_digest must be a lowercase SHA-256 digest")
        return self


class ManagedApprovalReviewConfig(ConfigurationModel):
    mode: Literal["off", "suggest", "auto"] = "off"
    agent_id: str = "reviewer_agent"
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=120.0)
    max_output_tokens: int = Field(default=4096, ge=64, le=8192)
    auto_max_risk: Literal["low", "medium"] = "medium"


class ManagedOperationalConfig(ConfigurationModel):
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_iterations: int = Field(default=32, ge=1, le=128)
    max_total_tool_calls: int = Field(default=50, ge=0, le=10_000)
    max_output_tokens: int = Field(default=4096, ge=64, le=131_072)
    context_window_budget: int = Field(default=65536, ge=512, le=10_000_000)
    task_capacity: int = Field(default=128, ge=1, le=10_000)
    principal_task_capacity: int = Field(default=32, ge=1, le=10_000)
    generation_drain_seconds: float = Field(default=120.0, ge=1.0, le=3600.0)
    # The configuration Agent is an opt-in control-plane capability.  Keep it
    # disabled for existing Nodes and enable it explicitly from Console.
    agent_configuration_enabled: bool = False

    @model_validator(mode="after")
    def validate_capacity(self) -> ManagedOperationalConfig:
        if self.principal_task_capacity > self.task_capacity:
            raise ValueError("Principal Task capacity cannot exceed global capacity")
        return self


class ManagedConfig(ConfigurationModel):
    schema_version: Literal[2] = 2
    providers: dict[SafeId, ManagedProviderConfig]
    models: dict[SafeId, ManagedModelConfig]
    model_deployments: dict[SafeId, ManagedModelDeploymentConfig] = Field(
        default_factory=dict
    )
    default_model: SafeId
    vision_model: str = ""
    fallback_model: str = ""
    fallback_enabled: bool = True
    agents: NodeAgentCatalog
    approval_review: ManagedApprovalReviewConfig = Field(
        default_factory=ManagedApprovalReviewConfig
    )
    skills: dict[SafeId, ManagedSkillConfig] = Field(default_factory=dict)
    mcp_servers: dict[SafeId, ManagedMCPConfig] = Field(default_factory=dict)
    operational: ManagedOperationalConfig = Field(
        default_factory=ManagedOperationalConfig
    )

    @model_validator(mode="after")
    def validate_references(self) -> ManagedConfig:
        if self.default_model not in self.models:
            raise ValueError("default_model must reference a configured model")
        if self.vision_model:
            if self.vision_model not in self.models:
                raise ValueError("vision_model must reference a configured model")
            if self.models[self.vision_model].supports_vision is not True:
                raise ValueError("vision_model must explicitly support image input")
        if self.fallback_model:
            if self.fallback_model not in self.models:
                raise ValueError("fallback_model must reference a configured model")
            if self.fallback_model == self.default_model:
                raise ValueError("fallback_model must differ from default_model")
        for alias, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(f"Model '{alias}' references an unknown provider")
        for deployment_id, deployment in self.model_deployments.items():
            if deployment.model_alias not in self.models:
                raise ValueError(
                    f"Model deployment '{deployment_id}' references an unknown model"
                )
            provider_id = self.models[deployment.model_alias].provider
            if self.providers[provider_id].driver == "workspace_remote":
                raise ValueError(
                    f"Model deployment '{deployment_id}' must use a Node-local Provider"
                )
        for agent_id, agent in self.agents.agents.items():
            binding = agent.model_binding
            if binding.ownership == "platform" and binding.model not in self.models:
                raise ValueError(
                    f"NodeAgent '{agent_id}' references an unknown model"
                )
        reviewer = self.agents.agents.get(self.approval_review.agent_id)
        if self.approval_review.mode != "off" and (
            reviewer is None or not reviewer.enabled
        ):
            raise ValueError(
                "Enabled approval review requires an enabled reviewer Agent"
            )
        return self

    @property
    def digest(self) -> str:
        payload = json.dumps(
            _canonical_value(self.model_dump(mode="python")),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class ConfigDraft(ConfigurationModel):
    draft_id: SafeId
    base_revision_id: SafeId
    document: ManagedConfig
    draft_version: int = Field(ge=1)
    updated_by: str
    updated_at: float = Field(ge=0.0)


class ConfigRevision(ConfigurationModel):
    revision_id: SafeId
    parent_revision_id: str = ""
    document: ManagedConfig
    config_digest: str
    change_summary: str = ""
    created_by: str
    created_at: float = Field(ge=0.0)


class ConfigControlState(ConfigurationModel):
    desired_revision_id: SafeId
    applied_revision_id: SafeId
    apply_status: Literal["idle", "applying", "failed"] = "idle"
    apply_error_code: str = ""
    updated_at: float = Field(ge=0.0)


class ConfigValidationIssue(ConfigurationModel):
    code: SafeId
    path: str
    message: str


class ConfigValidationResult(ConfigurationModel):
    valid: bool
    issues: tuple[ConfigValidationIssue, ...] = ()


class ConfigPublishResult(ConfigurationModel):
    revision: ConfigRevision
    state: ConfigControlState


class ConfigConflictError(RuntimeError):
    pass


class ConfigApplyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
