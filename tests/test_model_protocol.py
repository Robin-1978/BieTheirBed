"""Per-model wire protocol: one supplier, three Zen endpoint families."""
from __future__ import annotations

import pytest

from knoa_platform.config import AppConfig
from knoa_platform.agents.definitions import (
    ModelBindingSpec,
    NodeAgent,
    NodeAgentCatalog,
)
from knoa_platform.configuration.models import (
    ManagedConfig,
    ManagedModelConfig,
    ManagedProviderConfig,
    effective_model_driver,
)
from knoa_platform.model_adapter.profiles import resolve_profile


def _managed(
    provider_driver: str,
    model_protocol: str | None,
) -> ManagedConfig:
    if provider_driver == "workspace_remote":
        provider = ManagedProviderConfig(
            driver=provider_driver,  # type: ignore[arg-type]
            remote_deployment_id="deployment-a",
        )
    else:
        provider = ManagedProviderConfig(
            driver=provider_driver,  # type: ignore[arg-type]
            api_base="https://opencode.ai/zen/go/v1",
            api_key_ref="provider.supplier.api_key",
        )
    return ManagedConfig(
        providers={"supplier": provider},
        models={
            "primary": ManagedModelConfig(
                provider="supplier",
                model="m",
                protocol=model_protocol,  # type: ignore[arg-type]
            )
        },
        default_model="primary",
        agents=NodeAgentCatalog(
            agents={
                "knoa": NodeAgent(
                    kind="knoa",
                    display_name="Knoa Agent",
                    instructions="You are Knoa.",
                    visibility="user",
                    model_binding=ModelBindingSpec(
                        ownership="platform",
                        model="primary",
                    ),
                )
            },
            default_agent="knoa",
        ),
    )


def test_effective_driver_falls_back_to_provider() -> None:
    assert effective_model_driver("openai_compatible", None) == "openai_compatible"


def test_effective_driver_prefers_model_protocol() -> None:
    assert (
        effective_model_driver("openai_compatible", "openai_responses")
        == "openai_responses"
    )
    assert (
        effective_model_driver("openai_compatible", "anthropic") == "anthropic"
    )


def test_protocol_override_accepted_on_http_provider() -> None:
    doc = _managed("openai_compatible", "openai_responses")
    assert doc.models["primary"].protocol == "openai_responses"


def test_protocol_override_rejected_on_pinned_drivers() -> None:
    with pytest.raises(ValueError, match="owns"):
        _managed("llamacpp", "openai_responses")
    with pytest.raises(ValueError, match="owns"):
        _managed("workspace_remote", "anthropic")


def test_responses_profile_targets_responses_endpoint() -> None:
    profile = resolve_profile(
        "openai_responses",
        api_base="https://opencode.ai/zen/go/v1",
        api_key="secret",
    )
    assert profile.responses_style is True
    assert profile.anthropic_style is False
    assert profile.chat_url == "https://opencode.ai/zen/go/v1/responses"
    assert profile.health_url == "https://opencode.ai/zen/go/v1/models"
    assert profile.headers == {"Authorization": "Bearer secret"}


def test_anthropic_profile_honors_custom_base() -> None:
    profile = resolve_profile(
        "anthropic",
        api_base="https://opencode.ai/zen/go/v1",
        api_key="secret",
    )
    assert profile.anthropic_style is True
    assert profile.chat_url == "https://opencode.ai/zen/go/v1/messages"
    assert profile.headers["Authorization"] == "Bearer secret"


def test_legacy_resolve_model_honors_protocol() -> None:
    cfg = AppConfig(
        providers={
            "zen": {
                "driver": "openai_compatible",
                "api_base": "https://opencode.ai/zen/go/v1",
                "api_key": "test-secret",
            }
        },
        models={
            "luna": {
                "provider": "zen",
                "model": "gpt-5.6-luna",
                "protocol": "openai_responses",
            }
        },
        default_model="luna",
    )
    assert cfg.resolve_model("luna").driver == "openai_responses"
