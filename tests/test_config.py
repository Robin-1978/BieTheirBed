from __future__ import annotations

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
