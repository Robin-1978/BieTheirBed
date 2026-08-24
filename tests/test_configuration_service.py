from __future__ import annotations

import json
import sqlite3

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


def test_registry_prunes_history_but_preserves_live_references(tmp_path) -> None:
    registry = ConfigRegistry(tmp_path / "config.db")
    initial = registry.initialize(_managed(), actor="owner")
    draft = registry.create_draft(actor="owner")
    for iterations in range(33, 39):
        current = registry.current()
        changed = current.document.model_copy(
            update={
                "operational": current.document.operational.model_copy(
                    update={"max_iterations": iterations}
                )
            }
        )
        registry.adopt(changed, actor="owner", summary=f"revision-{iterations}")

    assert registry.prune_history(retain=2) == 4
    assert len(registry.history()) == 3
    assert registry.current().document.operational.max_iterations == 38
    assert registry.draft(draft.draft_id).base_revision_id == initial.revision_id
    assert registry.revision(initial.revision_id).revision_id == initial.revision_id


def test_registry_reinitializes_only_configuration_data_on_schema_change(
    tmp_path,
) -> None:
    database = tmp_path / "config.db"
    ids = iter(("revision-old", "draft-old", "revision-v2"))
    registry = ConfigRegistry(database, id_factory=lambda: next(ids))
    registry.initialize(_managed(), actor="owner")
    registry.create_draft(actor="owner")
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT document_json FROM config_revisions WHERE revision_id='revision-old'"
        ).fetchone()
        assert row is not None
        stored = json.loads(str(row[0]))
        stored["schema_version"] = 1
        db.execute(
            "UPDATE config_revisions SET document_json=? WHERE revision_id='revision-old'",
            (json.dumps(stored),),
        )

    revision = registry.initialize(_managed(), actor="owner")

    assert revision.revision_id == "revision-v2"
    assert revision.document.schema_version == 2
    assert revision.change_summary == "Initialize configuration schema v2"
    assert registry.state().applied_revision_id == "revision-v2"
    assert [item.revision_id for item in registry.history()] == ["revision-v2"]
    with pytest.raises(LookupError):
        registry.draft("draft-old")


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
