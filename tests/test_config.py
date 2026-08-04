from __future__ import annotations

import pytest

from pc_assistant.config import AppConfig, load_config


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.llm_provider == "llamacpp"
        assert cfg.llm_temperature == 0.7
        assert cfg.max_iterations == 8
        assert cfg.context_window_budget == 8192

    def test_masked_api_key_empty(self):
        cfg = AppConfig()
        assert cfg.masked_api_key() == ""

    def test_masked_api_key_short(self):
        cfg = AppConfig(llm_api_key="abc")
        assert cfg.masked_api_key() == "***"

    def test_masked_api_key_full(self):
        cfg = AppConfig(llm_api_key="sk-1234567890abcdef")
        result = cfg.masked_api_key()
        assert result.startswith("sk-1")
        assert result.endswith("cdef")
        assert "****" in result

    def test_set_field_valid(self):
        cfg = AppConfig()
        assert cfg.set_field("llm_temperature", "0.9") is True
        assert cfg.llm_temperature == 0.9

    def test_set_field_invalid(self):
        cfg = AppConfig()
        assert cfg.set_field("llm_temperature", "not_a_number") is False

    def test_set_field_unknown(self):
        cfg = AppConfig()
        assert cfg.set_field("nonexistent_field", "value") is False

    def test_set_field_int(self):
        cfg = AppConfig()
        assert cfg.set_field("max_iterations", "16") is True
        assert cfg.max_iterations == 16

    def test_set_field_string(self):
        cfg = AppConfig()
        assert cfg.set_field("llm_model_name", "gpt-4") is True
        assert cfg.llm_model_name == "gpt-4"

    def test_vision_provider_is_independent_from_main_provider(self):
        cfg = AppConfig(
            llm_provider="openai_compatible",
            llm_server_url="http://deepseek:8000",
            supports_vision=False,
            vision_provider="llamacpp",
            vision_server_url="http://127.0.0.1:8192",
            vision_model_name="qwen-vl",
        )
        assert cfg.llm_server_url == "http://deepseek:8000"
        assert cfg.vision_server_url == "http://127.0.0.1:8192"
        assert cfg.vision_model_name == "qwen-vl"

    def test_false_boolean_environment_override(self, monkeypatch, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text("vision_enabled: true\n", encoding="utf-8")
        monkeypatch.setenv("PC_VISION_ENABLED", "false")
        assert load_config(config).vision_enabled is False

    def test_multi_provider_model_catalog(self):
        cfg = AppConfig(
            providers={
                "ark_primary": {
                    "driver": "openai_compatible",
                    "api_base": "https://ark.example/api/coding/v3",
                    "api_key": "test-key",
                },
                "local_vision": {
                    "driver": "llamacpp",
                    "server_url": "http://127.0.0.1:8192",
                },
            },
            models={
                "main": {
                    "provider": "ark_primary",
                    "model": "coding-model",
                    "supports_vision": False,
                    "thinking": {"type": "enabled"},
                },
                "vision": {
                    "provider": "local_vision",
                    "model": "qwen-vl",
                    "supports_vision": True,
                },
            },
            default_model="main",
            vision_model="vision",
        )
        main = cfg.resolve_model()
        vision = cfg.resolve_vision_model()
        assert main.provider_name == "ark_primary"
        assert main.model == "coding-model"
        assert main.api_key == "test-key"
        assert main.supports_vision is False
        assert main.thinking is not None
        assert main.thinking.type == "enabled"
        assert vision.provider_name == "local_vision"
        assert vision.model == "qwen-vl"

    def test_multiple_accounts_for_same_vendor(self):
        cfg = AppConfig(
            providers={
                "ark_a": {"driver": "openai_compatible", "api_key": "key-a"},
                "ark_b": {"driver": "openai_compatible", "api_key": "key-b"},
            },
            models={
                "model_a": {"provider": "ark_a", "model": "model-a"},
                "model_b": {"provider": "ark_b", "model": "model-b"},
            },
            default_model="model_a",
        )
        assert cfg.resolve_model("model_a").api_key == "key-a"
        assert cfg.resolve_model("model_b").api_key == "key-b"

    def test_api_key_can_come_from_named_environment_variable(self, monkeypatch):
        monkeypatch.setenv("ARK_TEST_API_KEY", "secret-from-env")
        cfg = AppConfig(
            providers={
                "ark": {
                    "driver": "openai_compatible",
                    "api_key_env": "ARK_TEST_API_KEY",
                }
            },
            models={"main": {"provider": "ark", "model": "coding"}},
            default_model="main",
        )
        assert cfg.resolve_model().api_key == "secret-from-env"

    def test_api_key_env_rejects_literal_secret_without_echoing_it(self):
        cfg = AppConfig(
            providers={
                "ark": {
                    "driver": "openai_compatible",
                    "api_key_env": "secret-value-with-dashes",
                }
            },
            models={"main": {"provider": "ark", "model": "coding"}},
            default_model="main",
        )
        with pytest.raises(ValueError) as exc_info:
            cfg.resolve_model()
        assert "environment variable name" in str(exc_info.value)
        assert "secret-value-with-dashes" not in str(exc_info.value)

    def test_model_must_reference_known_provider(self):
        with pytest.raises(ValueError, match="unknown provider"):
            AppConfig(
                models={"main": {"provider": "missing", "model": "coding"}},
                default_model="main",
            )

    def test_loads_user_config_outside_working_directory(self, monkeypatch, tmp_path):
        app_home = tmp_path / ".pc-assistant"
        user_config = app_home / "config" / "local.yaml"
        user_config.parent.mkdir(parents=True)
        user_config.write_text("max_iterations: 19\n", encoding="utf-8")
        monkeypatch.setenv("PC_ASSISTANT_HOME", str(app_home))
        monkeypatch.chdir(tmp_path)
        assert load_config().max_iterations == 19

    def test_explicit_config_overrides_user_config(self, monkeypatch, tmp_path):
        app_home = tmp_path / ".pc-assistant"
        user_config = app_home / "config" / "local.yaml"
        user_config.parent.mkdir(parents=True)
        user_config.write_text("max_iterations: 19\n", encoding="utf-8")
        explicit = tmp_path / "selected.yaml"
        explicit.write_text("max_iterations: 23\n", encoding="utf-8")
        monkeypatch.setenv("PC_ASSISTANT_HOME", str(app_home))
        cfg = load_config(explicit)
        assert cfg.max_iterations == 23
        assert cfg.source_config_path == str(explicit.resolve())
