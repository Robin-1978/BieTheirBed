from __future__ import annotations

import pytest

from knoa_platform.agents.definitions import (
    AgentDefinition,
    AgentDefinitionResolver,
    AgentNotCallableError,
    AgentProfile,
    AgentSystemConfig,
    DelegationPolicy,
    ModelBindingSpec,
    RuntimeSpec,
)


def _config() -> AgentSystemConfig:
    return AgentSystemConfig(
        runtime_specs={
            "native-main": RuntimeSpec(
                implementation="native",
                model_binding=ModelBindingSpec(
                    ownership="platform",
                    model="primary_model",
                ),
            ),
            "codex-default": RuntimeSpec(
                implementation="codex",
                model_binding=ModelBindingSpec(ownership="runtime"),
                command=("codex", "app-server"),
                native_capabilities=frozenset(
                    {"workspace_read", "command_execution"}
                ),
                instruction_authority="required",
            ),
        },
        profiles={
            "assistant": AgentProfile(
                display_name="Knoa",
                instructions="You are Knoa.",
                allowed_platform_tools=frozenset({"read_file", "web_search"}),
                platform_capability_ceiling=frozenset(
                    {"host_read", "network", "shell"}
                ),
                visibility="user",
                delegation=DelegationPolicy(
                    allowed=True,
                    targets=frozenset({"codex"}),
                    max_depth=1,
                    max_children=3,
                    max_parallel_children=3,
                    max_deadline_seconds=600,
                ),
            ),
            "coder": AgentProfile(
                display_name="Coder",
                instructions="Work inside the repository.",
                runtime_native_capability_ceiling=frozenset(
                    {"workspace_read", "command_execution"}
                ),
                visibility="delegate",
                delegation=DelegationPolicy(
                    allowed=True,
                    targets=frozenset({"codex"}),
                    max_depth=3,
                    max_children=2,
                    max_parallel_children=1,
                    max_deadline_seconds=300,
                ),
            ),
        },
        agents={
            "knoa": AgentDefinition(
                runtime_spec_id="native-main",
                profile_id="assistant",
            ),
            "codex": AgentDefinition(
                runtime_spec_id="codex-default",
                profile_id="coder",
            ),
        },
        default_agent="knoa",
    )


def test_agent_system_validates_references_and_runtime_capabilities() -> None:
    config = _config()

    assert config.default_agent == "knoa"
    with pytest.raises(ValueError, match="unknown RuntimeSpec"):
        AgentSystemConfig(
            runtime_specs=config.runtime_specs,
            profiles=config.profiles,
            agents={
                "knoa": AgentDefinition(
                    runtime_spec_id="missing",
                    profile_id="assistant",
                )
            },
            default_agent="knoa",
        )


def test_resolver_enforces_visibility_and_parent_subset() -> None:
    resolver = AgentDefinitionResolver(_config(), config_revision_id="revision-2")
    parent = resolver.resolve_policy(
        None,
        invocation_kind="user",
        caller_id="personal:owner",
        principal_capabilities=frozenset(
            {"host_read", "host_write", "network", "shell"}
        ),
        available_tools=frozenset({"read_file", "write_file", "web_search"}),
        installed_skills=frozenset(),
    )

    child = resolver.resolve_policy(
        "codex",
        invocation_kind="delegate",
        caller_id="knoa",
        principal_capabilities=frozenset({"host_read", "host_write", "shell"}),
        available_tools=frozenset({"read_file", "write_file"}),
        installed_skills=frozenset(),
        requested_native_capabilities=frozenset(
            {"workspace_read", "command_execution"}
        ),
        parent=parent,
    )

    assert parent.allowed_platform_tools == frozenset({"read_file", "web_search"})
    assert child.allowed_platform_tools == frozenset()
    assert child.platform_capabilities == frozenset()
    assert child.runtime_native_capabilities == frozenset(
        {"workspace_read", "command_execution"}
    )
    assert child.delegation_max_depth == 0
    assert child.limits.max_children == 2
    assert child.limits.max_parallel_children == 1

    replayed = resolver.resolve_policy(
        "codex",
        invocation_kind="delegate",
        caller_id="knoa",
        principal_capabilities=frozenset({"host_read", "host_write", "shell"}),
        available_tools=frozenset({"read_file", "write_file"}),
        installed_skills=frozenset(),
        requested_capabilities=child.platform_capabilities,
        requested_tools=child.allowed_platform_tools,
        requested_skills=child.allowed_skills,
        requested_native_capabilities=child.runtime_native_capabilities,
    )
    assert replayed.platform_capabilities == child.platform_capabilities
    assert replayed.runtime_native_capabilities == child.runtime_native_capabilities
    assert child.config_revision_id == "revision-2"
    with pytest.raises(AgentNotCallableError):
        resolver.resolve_agent_id(
            "codex",
            invocation_kind="user",
            caller_id="personal:owner",
        )


def test_definition_digest_changes_with_profile() -> None:
    config = _config()
    before = AgentDefinitionResolver(config).definition_digest("knoa")
    profiles = dict(config.profiles)
    profiles["assistant"] = profiles["assistant"].model_copy(
        update={"instructions": "You are the updated Knoa."}
    )
    after = AgentDefinitionResolver(
        config.model_copy(update={"profiles": profiles})
    ).definition_digest("knoa")

    assert before != after
