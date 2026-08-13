from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from knoa_platform.extensions.models import MCP_SERVER_ID_PATTERN, MCPServerConfig
from knoa_platform.network_tls import is_loopback_host
from knoa_platform.runtime import default_runtime_root

_MAX_CONFIG_BYTES = 1024 * 1024


class ProviderConfig(BaseModel):
    """A named API account/endpoint.

    Provider names are user-defined (for example ``ark_coding_primary``), so
    the same vendor may be configured more than once with different keys.
    """

    driver: str = "openai_compatible"
    server_url: str = ""
    api_base: str = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    api_key_env: str = ""
    requires_api_key: bool | None = None
    timeout: float = 120.0

    def resolved_api_key(self) -> str:
        if self.api_key_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
                raise ValueError(
                    "api_key_env must contain an environment variable name; "
                    "store a literal credential in api_key instead"
                )
            value = os.environ.get(self.api_key_env, "")
            if not value:
                raise ValueError(
                    f"API key environment variable '{self.api_key_env}' is not set"
                )
            return value
        value = self.api_key.get_secret_value()
        required = (
            self.requires_api_key
            if self.requires_api_key is not None
            else self.driver in {"openai", "openai_compatible", "anthropic"}
        )
        if required and not value:
            raise ValueError("API key is required for this provider account")
        return value


class ThinkingConfig(BaseModel):
    """Provider-native thinking mode for compatible chat models."""

    type: Literal["enabled", "disabled", "auto"] = "enabled"


class AudioTranscriptionConfig(BaseModel):
    """Explicit mapping from Core audio ingress to one standard MCP Tool."""

    enabled: bool = False
    tool: str = ""
    max_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=45 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_tool_mapping(self) -> AudioTranscriptionConfig:
        normalized = self.tool.strip()
        if normalized and not re.fullmatch(
            r"mcp__[A-Za-z][A-Za-z0-9_-]{0,23}__[A-Za-z0-9_-]{1,128}",
            normalized,
        ):
            raise ValueError("Audio transcription tool must be a public MCP tool name")
        if self.enabled and not normalized:
            raise ValueError("Enabled audio transcription requires an MCP tool")
        self.tool = normalized
        return self


class ModelConfig(BaseModel):
    """A model exposed by one named provider account."""

    provider: str
    model: str
    supports_vision: bool | None = None
    # Provider/model context capacity. When set, it replaces the global
    # fallback budget for this model (subject to the completion reserve).
    context_window: int | None = None
    thinking: ThinkingConfig | None = None

    @field_validator("context_window", mode="before")
    @classmethod
    def _parse_context_window(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return value


class ResolvedModelConfig(BaseModel):
    alias: str
    provider_name: str
    driver: str
    server_url: str
    api_base: str
    api_key: str
    model: str
    supports_vision: bool | None
    context_window: int | None
    timeout: float
    thinking: ThinkingConfig | None = None


class WebhookRouteConfig(BaseModel):
    """One authenticated external webhook mapped to an owned Trigger."""

    trigger_id: str = Field(min_length=1, max_length=128)
    principal_id: str = Field(min_length=1, max_length=256)
    secret: SecretStr = Field(default_factory=lambda: SecretStr(""))
    secret_env: str = ""

    def resolved_secret(self) -> str:
        if self.secret_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.secret_env):
                raise ValueError("webhook secret_env must be an environment variable name")
            value = os.environ.get(self.secret_env, "")
            if not value:
                raise ValueError(
                    f"Webhook secret environment variable '{self.secret_env}' is not set"
                )
            if len(value.encode("utf-8")) < 32:
                raise ValueError("Webhook route secret must contain at least 32 bytes")
            return value
        value = self.secret.get_secret_value()
        if not value:
            raise ValueError("Webhook route secret is required")
        if len(value.encode("utf-8")) < 32:
            raise ValueError("Webhook route secret must contain at least 32 bytes")
        return value


class AgentConfig(BaseModel):
    """Configuration for one trusted Agent implementation.

    The mapping key is the stable ``agent_id``.  No runtime kind or arbitrary
    Python entry point is accepted; the composition root owns the small trusted
    implementation set.
    """

    enabled: bool = True
    max_concurrency: int = Field(default=1, ge=1, le=32)
    command: tuple[str, ...] = ()
    home: str = ""
    model: str = ""
    cwd: str = ""
    approval_policy: Literal["untrusted", "on-request", "never"] = "never"
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = (
        "read-only"
    )
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    max_line_bytes: int = Field(default=4 * 1024 * 1024, ge=4096, le=32 * 1024 * 1024)
    max_event_queue: int = Field(default=1024, ge=16, le=16_384)

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(part.strip() for part in value)
        if any(not part or "\x00" in part for part in normalized):
            raise ValueError("Agent command arguments must be non-empty safe strings")
        return normalized


class ApprovalReviewConfig(BaseModel):
    """Platform policy for the restricted system reviewer Agent."""

    mode: Literal["off", "suggest", "auto"] = "off"
    agent: str = "reviewer_agent"
    model: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0.0, le=120.0)
    max_output_tokens: int = Field(default=256, ge=64, le=1024)
    auto_max_risk: Literal["low", "medium"] = "medium"


class AppConfig(BaseModel):
    default_agent: str = "knoa"
    agents: dict[str, AgentConfig] = Field(
        default_factory=lambda: {
            "knoa": AgentConfig(enabled=True, max_concurrency=4),
            "codex": AgentConfig(
                enabled=False,
                command=("codex", "app-server"),
            ),
            "reviewer_agent": AgentConfig(
                enabled=False,
                max_concurrency=1,
            ),
        }
    )
    approval_review: ApprovalReviewConfig = Field(
        default_factory=ApprovalReviewConfig
    )
    # Multi-provider model catalog. Provider keys identify API accounts;
    # model keys are stable aliases used by the application.
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    default_model: str = ""
    fallback_enabled: bool = True
    fallback_model: str = ""

    # Single-model fallback used when no model catalog is configured.
    llm_provider: str = "llamacpp"
    llm_server_url: str = "http://127.0.0.1:8080"
    llm_model_name: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_temperature: float = 0.7
    llm_timeout: float = 120.0
    max_iterations: int = 8
    max_total_tool_calls: int = 50
    max_tokens: int = 1024
    shell_timeout: int = 30
    context_window_budget: int = 8192
    trace_enabled: bool = True
    llm_trace_log: str = "logs/llm_calls.jsonl"
    turn_trace_log: str = "logs/turns.jsonl"
    log_file: str = "logs/knoa_platform.json"
    runtime_root: str = Field(default_factory=lambda: str(default_runtime_root()))
    working_directory: str = Field(default_factory=os.getcwd)
    ui_theme: str = "catppuccin"
    service_host: str = "127.0.0.1"
    service_port: int = 9527
    service_token: str = ""
    owner_principal_id: str = "personal:owner"
    owner_principal_aliases: tuple[str, ...] = ("local",)
    webhook_enabled: bool = False
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 9528
    webhook_routes: dict[str, WebhookRouteConfig] = Field(default_factory=dict)
    gateway_enabled: bool = False
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 9529
    gateway_session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    gateway_artifact_max_bytes: int = Field(
        default=32 * 1024 * 1024,
        ge=1024 * 1024,
        le=48 * 1024 * 1024,
    )
    gateway_remote_enabled: bool = False
    gateway_public_url: str = ""
    gateway_tls_cert_file: str = ""
    gateway_tls_key_file: str = ""
    capability_mcp_host: str = "127.0.0.1"
    capability_mcp_port: int = 9530
    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: SecretStr = Field(default_factory=lambda: SecretStr(""))
    feishu_receive_id: str = ""
    attachment_ttl_seconds: int = 3600
    attachment_cleanup_interval_seconds: int = 300
    task_trace_retention_days: int = Field(default=90, ge=1, le=3650)
    conversation_detail_retention_days: int = Field(default=30, ge=1, le=3650)
    audio_transcription: AudioTranscriptionConfig = Field(
        default_factory=AudioTranscriptionConfig
    )
    supports_vision: bool | None = None
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    skill_directories: tuple[str, ...] = ()
    source_config_path: str = ""

    @model_validator(mode="after")
    def _validate_provider(self) -> AppConfig:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.default_agent):
            raise ValueError("default_agent must be a stable safe Agent ID")
        if set(self.agents) - {"knoa", "codex", "reviewer_agent"}:
            raise ValueError(
                "Only the trusted knoa, codex and reviewer_agent IDs are supported"
            )
        if "knoa" not in self.agents:
            raise ValueError("agents must configure the built-in knoa Agent")
        selected_agent = self.agents.get(self.default_agent)
        if selected_agent is None or not selected_agent.enabled:
            raise ValueError("default_agent must identify an enabled configured Agent")
        codex = self.agents.get("codex")
        if codex is not None and codex.enabled and not codex.command:
            raise ValueError("Enabled codex Agent requires a command")
        reviewer = self.agents.get("reviewer_agent")
        if reviewer is not None and reviewer.enabled and not (
            self.approval_review.model or reviewer.model
        ):
            raise ValueError("Enabled reviewer_agent requires a model")
        if self.approval_review.mode != "off":
            if self.approval_review.agent != "reviewer_agent":
                raise ValueError("approval_review agent must be reviewer_agent")
            if reviewer is None or not reviewer.enabled:
                raise ValueError(
                    "Enabled approval review requires enabled reviewer_agent"
                )
            model_alias = self.approval_review.model or reviewer.model
            if not model_alias:
                raise ValueError("Enabled approval review requires a reviewer model")
            if self.models and model_alias not in self.models:
                raise ValueError(f"Unknown reviewer model '{model_alias}'")
        if not 0 <= self.service_port <= 65535:
            raise ValueError("Core WebSocket service port must be between 0 and 65535")
        owner = self.owner_principal_id.strip()
        if not owner or len(owner) > 256 or any(char.isspace() for char in owner):
            raise ValueError("Owner principal ID must contain 1-256 non-space characters")
        aliases = tuple(alias.strip() for alias in self.owner_principal_aliases)
        if len(aliases) > 16 or any(
            not alias or len(alias) > 256 or any(char.isspace() for char in alias)
            for alias in aliases
        ):
            raise ValueError("Owner principal aliases must be 1-16 bounded IDs")
        self.owner_principal_id = owner
        self.owner_principal_aliases = tuple(dict.fromkeys(aliases))
        if not 0 <= self.webhook_port <= 65535:
            raise ValueError("Webhook port must be between 0 and 65535")
        if not 0 <= self.gateway_port <= 65535:
            raise ValueError("Secure Gateway port must be between 0 and 65535")
        if not 0 <= self.capability_mcp_port <= 65535:
            raise ValueError("Capability MCP port must be between 0 and 65535")
        if not is_loopback_host(self.capability_mcp_host):
            raise ValueError("Capability MCP endpoint must bind to loopback")
        invalid_webhook_ids = [
            route_id
            for route_id in self.webhook_routes
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", route_id)
        ]
        if invalid_webhook_ids:
            raise ValueError("Webhook route IDs must contain 1-64 safe characters")
        if self.webhook_enabled:
            if not self.webhook_routes:
                raise ValueError("Enabled webhook adapter requires at least one route")
            if self.service_port <= 0:
                raise ValueError("Enabled webhook adapter requires the Core TCP service")
            if self.webhook_port == self.service_port:
                raise ValueError("Webhook and Core service ports must differ")
        if self.gateway_enabled:
            if self.gateway_port == self.service_port:
                raise ValueError("Secure Gateway and Core service ports must differ")
            if self.webhook_enabled and self.gateway_port == self.webhook_port:
                raise ValueError("Secure Gateway and Webhook ports must differ")
            if not self.gateway_remote_enabled and not is_loopback_host(
                self.gateway_host
            ):
                raise ValueError(
                    "Secure Gateway requires remote TLS mode for non-loopback binding"
                )
        if self.gateway_remote_enabled:
            if not self.gateway_enabled:
                raise ValueError("Secure Gateway remote TLS mode requires Gateway")
            if not self.gateway_tls_cert_file or not self.gateway_tls_key_file:
                raise ValueError(
                    "Secure Gateway remote TLS mode requires certificate and key files"
                )
        public_url = self.gateway_public_url.strip().rstrip("/")
        if public_url:
            parsed = urlsplit(public_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Gateway public URL must be an absolute HTTP(S) URL")
            if parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError(
                    "Gateway public URL must not contain credentials, query or fragment"
                )
            if parsed.scheme != "https" and not is_loopback_host(
                parsed.hostname or ""
            ):
                raise ValueError("Non-loopback Gateway public URL must use HTTPS")
        self.gateway_public_url = public_url
        if self.feishu_enabled and (
            not self.feishu_app_id.strip()
            or not self.feishu_app_secret.get_secret_value().strip()
        ):
            raise ValueError("Enabled Feishu channel requires app_id and app_secret")
        invalid_mcp_ids = [
            server_id
            for server_id in self.mcp_servers
            if not MCP_SERVER_ID_PATTERN.fullmatch(server_id)
        ]
        if invalid_mcp_ids:
            raise ValueError("MCP server IDs must contain 1-24 safe characters")
        if self.models:
            if not self.default_model:
                raise ValueError("default_model is required when models are configured")
            if self.default_model not in self.models:
                raise ValueError(f"Unknown default_model '{self.default_model}'")
            if self.fallback_model and self.fallback_model not in self.models:
                raise ValueError(f"Unknown fallback_model '{self.fallback_model}'")
            if self.fallback_model == self.default_model:
                raise ValueError("fallback_model must differ from default_model")
            for alias, model in self.models.items():
                if model.provider not in self.providers:
                    raise ValueError(
                        f"Model '{alias}' references unknown provider '{model.provider}'"
                    )
            return self

        providers_needing_key = {"openai", "anthropic"}
        if self.llm_provider in providers_needing_key and not self.llm_api_key:
            raise ValueError(
                f"Provider '{self.llm_provider}' requires an API key. "
                "Set llm_api_key in config or KNOA_LLM_API_KEY environment variable."
            )
        return self

    def resolve_model(self, alias: str | None = None) -> ResolvedModelConfig:
        """Resolve a model alias into one complete transport configuration."""
        if self.models:
            selected = alias or self.default_model
            if selected not in self.models:
                raise ValueError(f"Unknown model '{selected}'")
            model = self.models[selected]
            endpoint = self.providers[model.provider]
            return ResolvedModelConfig(
                alias=selected,
                provider_name=model.provider,
                driver=endpoint.driver,
                server_url=endpoint.server_url or endpoint.api_base,
                api_base=endpoint.api_base,
                api_key=endpoint.resolved_api_key(),
                model=model.model,
                supports_vision=model.supports_vision,
                context_window=model.context_window,
                timeout=endpoint.timeout,
                thinking=model.thinking,
            )
        return ResolvedModelConfig(
            alias=alias or self.llm_model_name or "default",
            provider_name=self.llm_provider,
            driver=self.llm_provider,
            server_url=self.llm_server_url,
            api_base=self.llm_api_base,
            api_key=self.llm_api_key,
            model=self.llm_model_name,
            supports_vision=self.supports_vision,
            context_window=None,
            timeout=self.llm_timeout,
            thinking=None,
        )

    def resolve_fallback_model(self) -> ResolvedModelConfig | None:
        """Resolve the local model used after the primary provider fails."""
        if not self.fallback_enabled:
            return None
        if self.models:
            aliases = (
                [self.fallback_model]
                if self.fallback_model
                else [alias for alias in self.models if alias != self.default_model]
            )
            for alias in aliases:
                if not alias or alias not in self.models:
                    continue
                candidate = self.resolve_model(alias)
                if candidate.driver == "llamacpp":
                    return candidate
            return None
        if self.llm_provider == "llamacpp":
            return None
        return ResolvedModelConfig(
            alias="local-fallback",
            provider_name="llamacpp",
            driver="llamacpp",
            server_url="http://127.0.0.1:8192",
            api_base="",
            api_key="",
            model="",
            supports_vision=self.supports_vision,
            context_window=None,
            timeout=self.llm_timeout,
            thinking=None,
        )

    def masked_api_key(self) -> str:
        if not self.llm_api_key or len(self.llm_api_key) < 8:
            return "***" if self.llm_api_key else ""
        return self.llm_api_key[:4] + "****" + self.llm_api_key[-4:]

    def effective_context_window_budget(self) -> int:
        """Return the active model capacity, with the global budget as fallback."""
        model = self.resolve_model()
        if model.context_window and model.context_window > 0:
            return model.context_window
        return max(256, self.context_window_budget)

    def set_field(self, field_name: str, value: str) -> bool:
        fields = type(self).model_fields
        if field_name not in fields:
            return False
        annotation = fields[field_name].annotation
        if get_origin(annotation) is Union:
            args = [a for a in get_args(annotation) if a is not type(None)]
            annotation = args[0] if args else str
        if annotation in (list, dict, set):
            return False
        try:
            if annotation is bool:
                converted = value.strip().lower() in ("1", "true", "yes", "y", "on")
            elif annotation is int:
                converted = int(value)
            elif annotation is float:
                converted = float(value)
            else:
                converted = value
            setattr(self, field_name, converted)
            return True
        except (ValueError, TypeError):
            return False


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        raw = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ValueError(
            f"Config file exceeds {_MAX_CONFIG_BYTES} byte limit: {path}"
        )
    data = yaml.safe_load(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _env_overrides() -> dict[str, Any]:
    mapping: dict[str, tuple[str, type]] = {
        "KNOA_LLM_PROVIDER": ("llm_provider", str),
        "KNOA_DEFAULT_MODEL": ("default_model", str),
        "KNOA_FALLBACK_ENABLED": ("fallback_enabled", bool),
        "KNOA_FALLBACK_MODEL": ("fallback_model", str),
        "KNOA_LLM_SERVER_URL": ("llm_server_url", str),
        "KNOA_LLM_MODEL_NAME": ("llm_model_name", str),
        "KNOA_LLM_API_KEY": ("llm_api_key", str),
        "KNOA_LLM_API_BASE": ("llm_api_base", str),
        "KNOA_LLM_TEMPERATURE": ("llm_temperature", float),
        "KNOA_LLM_TIMEOUT": ("llm_timeout", float),
        "KNOA_MAX_ITERATIONS": ("max_iterations", int),
        "KNOA_MAX_TOTAL_TOOL_CALLS": ("max_total_tool_calls", int),
        "KNOA_SHELL_TIMEOUT": ("shell_timeout", int),
        "KNOA_CONTEXT_WINDOW_BUDGET": ("context_window_budget", int),
        "KNOA_TRACE_ENABLED": ("trace_enabled", bool),
        "KNOA_LLM_TRACE_LOG": ("llm_trace_log", str),
        "KNOA_TURN_TRACE_LOG": ("turn_trace_log", str),
        "KNOA_LOG_FILE": ("log_file", str),
        # Friendly application-home alias. KNOA_RUNTIME_ROOT remains the more
        # specific config override and wins when both are present.
        "KNOA_HOME": ("runtime_root", str),
        "KNOA_RUNTIME_ROOT": ("runtime_root", str),
        "KNOA_WORKING_DIRECTORY": ("working_directory", str),
        "KNOA_OWNER_PRINCIPAL_ID": ("owner_principal_id", str),
        "KNOA_WEBHOOK_ENABLED": ("webhook_enabled", bool),
        "KNOA_WEBHOOK_HOST": ("webhook_host", str),
        "KNOA_WEBHOOK_PORT": ("webhook_port", int),
        "KNOA_GATEWAY_ENABLED": ("gateway_enabled", bool),
        "KNOA_GATEWAY_HOST": ("gateway_host", str),
        "KNOA_GATEWAY_PORT": ("gateway_port", int),
        "KNOA_GATEWAY_REMOTE_ENABLED": ("gateway_remote_enabled", bool),
        "KNOA_GATEWAY_PUBLIC_URL": ("gateway_public_url", str),
        "KNOA_GATEWAY_TLS_CERT_FILE": ("gateway_tls_cert_file", str),
        "KNOA_GATEWAY_TLS_KEY_FILE": ("gateway_tls_key_file", str),
        "KNOA_FEISHU_ENABLED": ("feishu_enabled", bool),
        "KNOA_FEISHU_APP_ID": ("feishu_app_id", str),
        "KNOA_FEISHU_APP_SECRET": ("feishu_app_secret", str),
        "KNOA_FEISHU_RECEIVE_ID": ("feishu_receive_id", str),
        "KNOA_ATTACHMENT_TTL_SECONDS": ("attachment_ttl_seconds", int),
        "KNOA_ATTACHMENT_CLEANUP_INTERVAL_SECONDS": ("attachment_cleanup_interval_seconds", int),
        "KNOA_TASK_TRACE_RETENTION_DAYS": ("task_trace_retention_days", int),
        "KNOA_CONVERSATION_DETAIL_RETENTION_DAYS": (
            "conversation_detail_retention_days",
            int,
        ),
        "KNOA_SUPPORTS_VISION": ("supports_vision", bool),
    }
    overrides: dict[str, Any] = {}
    for env_key, (field_name, field_type) in mapping.items():
        raw = os.environ.get(env_key)
        if raw is not None:
            try:
                if field_type is bool:
                    overrides[field_name] = raw.strip().lower() in ("1", "true", "yes", "y", "on")
                else:
                    overrides[field_name] = field_type(raw)
            except (ValueError, TypeError):
                pass
    return overrides


def load_config(config_path: str | Path | None = None) -> AppConfig:
    # Layering, from lowest to highest priority:
    # project defaults -> per-user private config -> explicit --config -> env.
    # The user config location is independent of the current working directory.
    source_default = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
    packaged_default = Path(__file__).resolve().parent / "resources" / "default.yaml"
    default_path = source_default if source_default.is_file() else packaged_default
    explicit_path = Path(config_path).expanduser().resolve() if config_path is not None else None

    yaml_data = _load_yaml(default_path)
    user_path = default_runtime_root() / "config" / "local.yaml"
    user_data = _load_yaml(user_path)
    if user_data:
        yaml_data = {**yaml_data, **user_data}

    if explicit_path is not None:
        explicit_data = _load_yaml(explicit_path)
        if explicit_data:
            yaml_data = {**yaml_data, **explicit_data}
    if explicit_path is not None and not explicit_path.exists():
        import warnings
        warnings.warn(
            f"Config file not found: {explicit_path}. "
            "Using defaults, user config, and environment variables."
        )

    env_data = _env_overrides()
    merged: dict[str, Any] = {**yaml_data, **env_data}
    # Only propagate a path to the daemon when the user explicitly selected
    # one. The default daemon independently loads the same per-user config.
    merged["source_config_path"] = str(explicit_path) if explicit_path is not None else ""
    return AppConfig(**merged)
