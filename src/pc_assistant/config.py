from __future__ import annotations

import os
from pathlib import Path
from typing import Any, get_args, get_origin, Union

import yaml
from pydantic import BaseModel, Field, model_validator

from pc_assistant.platform_ import get_default_dangerous_commands, get_default_protected_paths


class AppConfig(BaseModel):
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
    source_config_path: str = ""

    @model_validator(mode="after")
    def _validate_provider(self) -> "AppConfig":
        providers_needing_key = {"openai", "anthropic"}
        if self.llm_provider in providers_needing_key and not self.llm_api_key:
            raise ValueError(
                f"Provider '{self.llm_provider}' requires an API key. "
                "Set llm_api_key in config or PC_LLM_API_KEY environment variable."
            )
        return self

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
        "PC_WORKING_DIRECTORY": ("working_directory", str),
        "PC_FEISHU_ENABLED": ("feishu_enabled", bool),
        "PC_FEISHU_APP_ID": ("feishu_app_id", str),
        "PC_FEISHU_APP_SECRET": ("feishu_app_secret", str),
        "PC_FEISHU_RECEIVE_ID": ("feishu_receive_id", str),
        "PC_FEISHU_RECEIVE_ID_TYPE": ("feishu_receive_id_type", str),
    }
    overrides: dict[str, Any] = {}
    for env_key, (field_name, field_type) in mapping.items():
        raw = os.environ.get(env_key)
        if raw is not None:
            try:
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
    if config_path is None:
        config_path = Path("config/default.yaml")
    else:
        config_path = Path(config_path)
    yaml_data = _load_yaml(config_path)
    if not yaml_data and not config_path.exists():
        import warnings
        warnings.warn(f"Config file not found: {config_path}. Using defaults and environment variables.")

    local_path = Path("config/local.yaml")
    if local_path.exists():
        local_data = _load_yaml(local_path)
        if local_data:
            yaml_data = {**yaml_data, **local_data}

    env_data = _env_overrides()
    merged: dict[str, Any] = {**yaml_data, **env_data}
    merged["source_config_path"] = str(config_path)
    return AppConfig(**merged)
