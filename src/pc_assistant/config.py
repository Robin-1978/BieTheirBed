from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, get_args, get_origin, Literal, Union

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator

from pc_assistant.platform_ import get_default_dangerous_commands, get_default_protected_paths
from pc_assistant.runtime import default_runtime_root


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


class ModelConfig(BaseModel):
    """A model exposed by one named provider account."""

    provider: str
    model: str
    supports_vision: bool | None = None
    token_family: str = ""
    thinking: ThinkingConfig | None = None


class ResolvedModelConfig(BaseModel):
    alias: str
    provider_name: str
    driver: str
    server_url: str
    api_base: str
    api_key: str
    model: str
    supports_vision: bool | None
    token_family: str
    timeout: float
    thinking: ThinkingConfig | None = None


class AppConfig(BaseModel):
    # Multi-provider model catalog. Provider keys identify API accounts;
    # model keys are stable aliases used by the application.
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    default_model: str = ""
    vision_model: str = ""

    # Single-model fallback used when no model catalog is configured.
    llm_provider: str = "llamacpp"
    llm_server_url: str = "http://127.0.0.1:8080"
    llm_model_name: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_temperature: float = 0.7
    llm_timeout: float = 120.0
    max_iterations: int = 8
    max_consecutive_same_tool: int = 3
    max_total_tool_calls: int = 50
    max_consecutive_tool_calls: int = 50
    max_tokens: int = 1024
    shell_timeout: int = 30
    context_window_budget: int = 8192
    llm_compact_enabled: bool = False
    token_family: str = ""
    max_sessions: int = 100
    trace_enabled: bool = True
    llm_trace_log: str = "logs/llm_calls.jsonl"
    turn_trace_log: str = "logs/turns.jsonl"
    evidence_policy_enabled: bool = True
    dangerous_commands: list[str] = Field(default_factory=get_default_dangerous_commands)
    protected_paths: list[str] = Field(default_factory=get_default_protected_paths)
    log_file: str = "logs/pc_assistant.json"
    runtime_root: str = Field(default_factory=lambda: str(default_runtime_root()))
    working_directory: str = Field(default_factory=os.getcwd)
    reflection_enabled: bool = False
    reflection_threshold: int = 7
    ui_theme: str = "catppuccin"
    service_host: str = "127.0.0.1"
    service_port: int = 0
    service_token: str = ""
    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_receive_id: str = ""
    feishu_receive_id_type: str = "open_id"
    remote_unlock_enabled: bool = False
    remote_unlock_allowed_open_ids: list[str] = Field(default_factory=list)
    remote_unlock_totp_secret_file: str = "secrets/unlock.totp"
    remote_unlock_totp_period_seconds: int = 30
    remote_unlock_max_attempts: int = 3
    remote_unlock_lockout_seconds: int = 300
    vision_max_side: int = 1280
    vision_jpeg_quality: int = 70
    vision_enabled: bool = True
    vision_provider: str = "llamacpp"
    vision_server_url: str = "http://127.0.0.1:8192"
    vision_model_name: str = ""
    vision_api_key: str = ""
    vision_api_base: str = ""
    vision_timeout: float = 120.0
    vision_max_tokens: int = 1024
    attachment_ttl_seconds: int = 3600
    attachment_cleanup_interval_seconds: int = 300
    supports_vision: bool | None = None
    screen_grid_enabled: bool = False
    screen_verify_enabled: bool = False
    ui_backend: str = "auto"
    source_config_path: str = ""

    @model_validator(mode="after")
    def _validate_provider(self) -> "AppConfig":
        if (
            self.service_port > 0
            and self.service_host not in {"127.0.0.1", "localhost", "::1"}
            and not self.service_token
        ):
            raise ValueError(
                "service_token is required when service_host is not loopback"
            )
        if self.models:
            if not self.default_model:
                raise ValueError("default_model is required when models are configured")
            if self.default_model not in self.models:
                raise ValueError(f"Unknown default_model '{self.default_model}'")
            if self.vision_model and self.vision_model not in self.models:
                raise ValueError(f"Unknown vision_model '{self.vision_model}'")
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
                "Set llm_api_key in config or PC_LLM_API_KEY environment variable."
            )
        if (
            self.vision_enabled
            and self.vision_provider in providers_needing_key
            and not self.vision_api_key
        ):
            raise ValueError(
                f"Vision provider '{self.vision_provider}' requires an API key. "
                "Set vision_api_key or PC_VISION_API_KEY."
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
                token_family=model.token_family,
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
            token_family=self.token_family,
            timeout=self.llm_timeout,
            thinking=None,
        )

    def resolve_vision_model(self) -> ResolvedModelConfig:
        if self.models and self.vision_model:
            return self.resolve_model(self.vision_model)
        return ResolvedModelConfig(
            alias=self.vision_model_name or "vision",
            provider_name=self.vision_provider,
            driver=self.vision_provider,
            server_url=self.vision_server_url,
            api_base=self.vision_api_base,
            api_key=self.vision_api_key,
            model=self.vision_model_name,
            supports_vision=True,
            token_family=self.token_family,
            timeout=self.vision_timeout,
            thinking=None,
        )

    def masked_api_key(self) -> str:
        if not self.llm_api_key or len(self.llm_api_key) < 8:
            return "***" if self.llm_api_key else ""
        return self.llm_api_key[:4] + "****" + self.llm_api_key[-4:]

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
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _env_overrides() -> dict[str, Any]:
    mapping: dict[str, tuple[str, type]] = {
        "PC_LLM_PROVIDER": ("llm_provider", str),
        "PC_DEFAULT_MODEL": ("default_model", str),
        "PC_VISION_MODEL": ("vision_model", str),
        "PC_LLM_SERVER_URL": ("llm_server_url", str),
        "PC_LLM_MODEL_NAME": ("llm_model_name", str),
        "PC_LLM_API_KEY": ("llm_api_key", str),
        "PC_LLM_API_BASE": ("llm_api_base", str),
        "PC_LLM_TEMPERATURE": ("llm_temperature", float),
        "PC_LLM_TIMEOUT": ("llm_timeout", float),
        "PC_MAX_ITERATIONS": ("max_iterations", int),
        "PC_MAX_CONSECUTIVE_SAME_TOOL": ("max_consecutive_same_tool", int),
        "PC_MAX_TOTAL_TOOL_CALLS": ("max_total_tool_calls", int),
        "PC_MAX_CONSECUTIVE_TOOL_CALLS": ("max_consecutive_tool_calls", int),
        "PC_SHELL_TIMEOUT": ("shell_timeout", int),
        "PC_CONTEXT_WINDOW_BUDGET": ("context_window_budget", int),
        "PC_LLM_COMPACT_ENABLED": ("llm_compact_enabled", bool),
        "PC_TOKEN_FAMILY": ("token_family", str),
        "PC_MAX_SESSIONS": ("max_sessions", int),
        "PC_TRACE_ENABLED": ("trace_enabled", bool),
        "PC_LLM_TRACE_LOG": ("llm_trace_log", str),
        "PC_TURN_TRACE_LOG": ("turn_trace_log", str),
        "PC_EVIDENCE_POLICY_ENABLED": ("evidence_policy_enabled", bool),
        "PC_LOG_FILE": ("log_file", str),
        # Friendly application-home alias. PC_RUNTIME_ROOT remains the more
        # specific config override and wins when both are present.
        "PC_ASSISTANT_HOME": ("runtime_root", str),
        "PC_RUNTIME_ROOT": ("runtime_root", str),
        "PC_WORKING_DIRECTORY": ("working_directory", str),
        "PC_FEISHU_ENABLED": ("feishu_enabled", bool),
        "PC_FEISHU_APP_ID": ("feishu_app_id", str),
        "PC_FEISHU_APP_SECRET": ("feishu_app_secret", str),
        "PC_FEISHU_RECEIVE_ID": ("feishu_receive_id", str),
        "PC_FEISHU_RECEIVE_ID_TYPE": ("feishu_receive_id_type", str),
        "PC_VISION_MAX_SIDE": ("vision_max_side", int),
        "PC_VISION_JPEG_QUALITY": ("vision_jpeg_quality", int),
        "PC_VISION_ENABLED": ("vision_enabled", bool),
        "PC_VISION_PROVIDER": ("vision_provider", str),
        "PC_VISION_SERVER_URL": ("vision_server_url", str),
        "PC_VISION_MODEL_NAME": ("vision_model_name", str),
        "PC_VISION_API_KEY": ("vision_api_key", str),
        "PC_VISION_API_BASE": ("vision_api_base", str),
        "PC_VISION_TIMEOUT": ("vision_timeout", float),
        "PC_VISION_MAX_TOKENS": ("vision_max_tokens", int),
        "PC_ATTACHMENT_TTL_SECONDS": ("attachment_ttl_seconds", int),
        "PC_ATTACHMENT_CLEANUP_INTERVAL_SECONDS": ("attachment_cleanup_interval_seconds", int),
        "PC_SUPPORTS_VISION": ("supports_vision", bool),
        "PC_SCREEN_GRID_ENABLED": ("screen_grid_enabled", bool),
        "PC_SCREEN_VERIFY_ENABLED": ("screen_verify_enabled", bool),
        "PC_UI_BACKEND": ("ui_backend", str),
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
    raw_dangerous = os.environ.get("PC_DANGEROUS_COMMANDS")
    if raw_dangerous:
        overrides["dangerous_commands"] = [
            cmd.strip() for cmd in raw_dangerous.split(",") if cmd.strip()
        ]
    raw_protected = os.environ.get("PC_PROTECTED_PATHS")
    if raw_protected:
        overrides["protected_paths"] = [
            p.strip() for p in raw_protected.split(",") if p.strip()
        ]
    return overrides


def load_config(config_path: str | Path | None = None) -> AppConfig:
    # Layering, from lowest to highest priority:
    # project defaults -> per-user private config -> explicit --config -> env.
    # The user config location is independent of the current working directory.
    default_path = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
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
