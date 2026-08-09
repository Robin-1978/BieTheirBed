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
        assert cfg.audio_transcription.enabled is False
        assert cfg.gateway_enabled is False
        assert cfg.gateway_artifact_max_bytes == 32 * 1024 * 1024

    def test_audio_transcription_requires_public_mcp_tool(self):
        with pytest.raises(ValueError, match="requires an MCP tool"):
            AppConfig(audio_transcription={"enabled": True})
        with pytest.raises(ValueError, match="public MCP tool name"):
            AppConfig(
                audio_transcription={
                    "enabled": True,
                    "tool": "builtin_transcribe",
                }
            )

    def test_gateway_port_must_not_conflict_with_enabled_services(self):
        with pytest.raises(ValueError, match="Gateway and Core"):
            AppConfig(gateway_enabled=True, gateway_port=9527)

        with pytest.raises(ValueError, match="Gateway and Webhook"):
            AppConfig(
                gateway_enabled=True,
                gateway_port=9528,
                webhook_enabled=True,
                webhook_routes={
                    "jira": {
                        "trigger_id": "trigger-a",
                        "principal_id": "personal:owner",
                        "secret": "s" * 32,
                    }
                },
            )

        config = AppConfig(gateway_enabled=True, gateway_port=9528)
        assert config.gateway_port == 9528

    def test_gateway_remote_binding_requires_explicit_tls_configuration(self):
        with pytest.raises(ValueError, match="remote TLS mode for non-loopback"):
            AppConfig(gateway_enabled=True, gateway_host="0.0.0.0")

        with pytest.raises(ValueError, match="requires Gateway"):
            AppConfig(
                gateway_remote_enabled=True,
                gateway_tls_cert_file="/tmp/cert.pem",
                gateway_tls_key_file="/tmp/key.pem",
            )

        with pytest.raises(ValueError, match="certificate and key"):
            AppConfig(gateway_enabled=True, gateway_remote_enabled=True)

    def test_model_context_window_overrides_global_fallback(self):
        cfg = AppConfig(
            providers={"api": {"driver": "openai_compatible", "api_key": "k"}},
            models={"main": {"provider": "api", "model": "m", "context_window": 131072}},
            default_model="main",
        )
        assert cfg.effective_context_window_budget() == 131072

    def test_model_context_window_accepts_comma_formatted_value(self):
        cfg = AppConfig(
            providers={"api": {"driver": "openai_compatible", "api_key": "k"}},
            models={"main": {"provider": "api", "model": "m", "context_window": "131,072"}},
            default_model="main",
        )
        assert cfg.models["main"].context_window == 131072

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
        )
        main = cfg.resolve_model()
        vision = cfg.resolve_model("vision")
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

    def test_fallback_model_must_differ_from_primary(self):
        with pytest.raises(ValueError, match="must differ"):
            AppConfig(
                providers={
                    "local": {
                        "driver": "llamacpp",
                        "server_url": "http://127.0.0.1:8192",
                    }
                },
                models={
                    "main": {
                        "provider": "local",
                        "model": "local-model",
                    }
                },
                default_model="main",
                fallback_model="main",
            )

    def test_automatic_fallback_never_reuses_primary(self):
        cfg = AppConfig(
            providers={
                "local": {
                    "driver": "llamacpp",
                    "server_url": "http://127.0.0.1:8192",
                }
            },
            models={
                "main": {
                    "provider": "local",
                    "model": "local-model",
                }
            },
            default_model="main",
            fallback_enabled=True,
        )

        assert cfg.resolve_fallback_model() is None

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

    def test_oversized_config_is_rejected_before_yaml_parse(self, tmp_path):
        explicit = tmp_path / "oversized.yaml"
        explicit.write_bytes(b"x" * (1024 * 1024 + 1))

        with pytest.raises(ValueError, match="1048576 byte limit"):
            load_config(explicit)
