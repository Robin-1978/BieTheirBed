from __future__ import annotations

import pytest

from knoa_platform.agents.definitions import (
    ModelBindingSpec,
    NodeAgent,
    NodeAgentCatalog,
)
from knoa_platform.configuration import (
    ConfigApplyError,
    ConfigConflictError,
    ConfigRegistry,
    ConfigurationService,
    ManagedConfig,
    ManagedModelConfig,
    ManagedProviderConfig,
)


def _managed() -> ManagedConfig:
    return ManagedConfig(
        providers={
            "local": ManagedProviderConfig(
                driver="llamacpp",
                server_url="http://127.0.0.1:8192",
                requires_api_key=False,
            )
        },
        models={
            "primary": ManagedModelConfig(
                provider="local",
                model="qwen",
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


def test_registry_persists_draft_publish_and_rollback(tmp_path) -> None:
    ids = iter(("revision-1", "draft-1", "revision-2", "revision-3"))
    registry = ConfigRegistry(tmp_path / "config.db", id_factory=lambda: next(ids))
    initial = registry.initialize(_managed(), actor="owner")
    draft = registry.create_draft(actor="owner")
    changed = draft.document.model_copy(
        update={
            "operational": draft.document.operational.model_copy(
                update={"max_iterations": 48}
            )
        }
    )
    draft = registry.replace_draft(
        draft.draft_id,
        changed,
        expected_version=1,
        actor="owner",
    )
    revision = registry.publish_draft(
        draft.draft_id,
        expected_version=2,
        actor="owner",
        summary="Increase iteration ceiling",
    )

    assert initial.revision_id == "revision-1"
    assert revision.parent_revision_id == initial.revision_id
    assert registry.state().apply_status == "applying"
    assert (
        registry.mark_applied(revision.revision_id).applied_revision_id == "revision-2"
    )
    rollback = registry.rollback(
        initial.revision_id,
        actor="owner",
        summary="Rollback",
    )
    assert rollback.document.operational.max_iterations == 32


def test_registry_rejects_stale_draft_updates(tmp_path) -> None:
    registry = ConfigRegistry(tmp_path / "config.db")
    registry.initialize(_managed(), actor="owner")
    draft = registry.create_draft(actor="owner")

    with pytest.raises(ConfigConflictError):
        registry.replace_draft(
            draft.draft_id,
            draft.document,
            expected_version=2,
            actor="owner",
        )


@pytest.mark.asyncio
async def test_configuration_service_keeps_old_applied_revision_on_failure(
    tmp_path,
) -> None:
    async def fail_apply(_previous, _revision) -> None:
        raise ConfigApplyError("runtime_unhealthy", "Runtime failed health check")

    service = ConfigurationService(
        ConfigRegistry(tmp_path / "config.db"),
        _managed(),
        bootstrap_actor="owner",
        applier=fail_apply,
    )
    before = service.current()
    draft = service.create_draft(actor="owner")
    changed = draft.document.model_copy(
        update={
            "operational": draft.document.operational.model_copy(
                update={"max_iterations": 64}
            )
        }
    )
    draft = service.replace_draft(
        draft.draft_id,
        changed,
        expected_version=1,
        actor="owner",
    )
    result = await service.publish(
        draft.draft_id,
        expected_version=draft.draft_version,
        actor="owner",
        summary="Apply unhealthy Runtime",
    )

    assert result.state.apply_status == "failed"
    assert result.state.applied_revision_id == before.revision_id
    assert service.current().revision_id == before.revision_id
