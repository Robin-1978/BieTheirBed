from __future__ import annotations

import pytest

from knoa_platform.agents.definitions import (
    AgentNotCallableError,
    DelegationPolicy,
    ModelBindingSpec,
    NodeAgent,
    NodeAgentCatalog,
    NodeAgentResolver,
)


def _config() -> NodeAgentCatalog:
    return NodeAgentCatalog(
        agents={
            "knoa": NodeAgent(
                kind="knoa",
                display_name="Knoa Agent",
                instructions="You are Knoa.",
                visibility="user",
                model_binding=ModelBindingSpec(
                    ownership="platform",
                    model="primary_model",
                ),
                allowed_platform_tools=frozenset({"read_file", "web_search"}),
                platform_capability_ceiling=frozenset(
                    {"host_read", "network", "shell"}
                ),
                delegation=DelegationPolicy(
                    allowed=True,
                    targets=frozenset({"codex"}),
                    max_depth=1,
                    max_children=3,
                    max_parallel_children=3,
                    max_deadline_seconds=600,
                ),
            ),
            "codex": NodeAgent(
                kind="codex",
                display_name="Codex Agent",
                instructions="Work inside the repository.",
                visibility="delegate",
                model_binding=ModelBindingSpec(ownership="runtime"),
                command=("codex", "app-server"),
                native_capability_ceiling=frozenset(
                    {"workspace_read", "command_execution"}
                ),
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
        default_agent="knoa",
    )


def test_node_agent_catalog_validates_default_and_targets() -> None:
    config = _config()

    assert config.default_agent == "knoa"
    with pytest.raises(ValueError, match="default_agent"):
        NodeAgentCatalog(agents=config.agents, default_agent="missing")

    hidden_default = config.agents["codex"].model_copy(update={"enabled": True})
    with pytest.raises(ValueError, match="user-visible"):
        NodeAgentCatalog(
            agents={**config.agents, "codex": hidden_default},
            default_agent="codex",
        )

    invalid_parent = config.agents["knoa"].model_copy(
        update={
            "delegation": config.agents["knoa"].delegation.model_copy(
                update={"targets": frozenset({"knoa"})}
            )
        }
    )
    with pytest.raises(ValueError, match="delegate-visible"):
        NodeAgentCatalog(
            agents={**config.agents, "knoa": invalid_parent},
            default_agent="knoa",
        )


def test_resolver_enforces_visibility_and_parent_subset() -> None:
    resolver = NodeAgentResolver(_config(), config_revision_id="revision-2")
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
    assert child.config_revision_id == "revision-2"
    with pytest.raises(AgentNotCallableError):
        resolver.resolve_agent_id(
            "codex",
            invocation_kind="user",
            caller_id="personal:owner",
        )


def test_node_agent_digest_changes_with_prompt() -> None:
    config = _config()
    before = NodeAgentResolver(config).agent_digest("knoa")
    agents = dict(config.agents)
    agents["knoa"] = agents["knoa"].model_copy(
        update={"instructions": "You are the updated Knoa."}
    )
    after = NodeAgentResolver(
        config.model_copy(update={"agents": agents})
    ).agent_digest("knoa")

    assert before != after
