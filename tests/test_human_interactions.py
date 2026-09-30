from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from knoa_agent_contracts import InteractionRequested
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.interactions import (
    HumanInteractionRepository,
    HumanInteractionService,
)


@pytest.mark.asyncio
async def test_generic_interaction_persists_validates_and_wakes_waiter(
    tmp_path: Path,
) -> None:
    repository = HumanInteractionRepository(
        tmp_path / "platform.db",
        id_factory=lambda: "interaction-a",
    )
    changed: list[tuple[str, str, str]] = []

    async def observe(interaction) -> None:
        changed.append(
            (interaction.owner_kind, interaction.owner_id, interaction.state)
        )

    service = HumanInteractionService(repository, changed=observe)
    port = service.for_owner("conversation_turn")
    event = InteractionRequested(
        runtime_session_ref="runtime-session-a",
        runtime_turn_ref="runtime-turn-a",
        occurred_at=1.0,
        interaction_id="runtime-input-a",
        interaction_epoch=1,
        kind="user_input",
        display={"title": "Choose", "fields": []},
        resolution_schema={
            "type": "object",
            "properties": {"target": {"type": "string", "enum": ["a", "b"]}},
            "required": ["target"],
            "additionalProperties": False,
        },
    )
    handle = await port.begin(
        RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        "turn-a",
        event,
    )

    with pytest.raises(ValueError):
        await service.resolve(
            "principal-a", "interaction-a", {"target": "not-an-option"}
        )
    interaction, resolved = await service.resolve(
        "principal-a",
        "interaction-a",
        {"target": "b"},
        resolved_by="device-a",
    )

    assert resolved is True
    assert interaction.resolution == {"target": "b"}
    assert await handle.wait() == {"target": "b"}
    assert repository.list_owner("principal-a", "conversation_turn", "turn-a") == (
        interaction,
    )
    assert changed == [
        ("conversation_turn", "turn-a", "pending"),
        ("conversation_turn", "turn-a", "resolved"),
    ]


@pytest.mark.asyncio
async def test_start_marks_persisted_pending_interaction_runtime_lost(
    tmp_path: Path,
) -> None:
    database = tmp_path / "platform.db"
    repository = HumanInteractionRepository(
        database,
        id_factory=lambda: "interaction-a",
        clock=lambda: 10.0,
    )
    original = HumanInteractionService(repository)
    event = InteractionRequested(
        runtime_session_ref="runtime-session-a",
        runtime_turn_ref="runtime-turn-a",
        occurred_at=1.0,
        interaction_id="runtime-input-a",
        interaction_epoch=1,
        kind="user_input",
        display={"title": "Choose", "fields": []},
        resolution_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
    )
    await original.for_owner("task_execution").begin(
        RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        "task-a",
        event,
    )
    await original.close()

    changed = []

    async def observe(interaction) -> None:
        changed.append(interaction)

    restarted = HumanInteractionService(repository, changed=observe)
    recovered = await restarted.start()

    assert recovered == tuple(changed)
    assert len(recovered) == 1
    assert recovered[0].state == "runtime_lost"
    assert recovered[0].resolved_at == 10.0
    assert recovered[0].resolved_by == "platform_restart"
    interaction, resolved = await restarted.resolve(
        "principal-a",
        "interaction-a",
        {"target": "a"},
    )
    assert resolved is False
    assert interaction.state == "runtime_lost"


@pytest.mark.asyncio
async def test_device_location_interaction_accepts_an_automatic_app_response(
    tmp_path: Path,
) -> None:
    repository = HumanInteractionRepository(
        tmp_path / "platform.db",
        id_factory=lambda: "interaction-location",
    )
    service = HumanInteractionService(repository)
    event = InteractionRequested(
        runtime_session_ref="runtime-session-a",
        runtime_turn_ref="runtime-turn-a",
        occurred_at=1.0,
        interaction_id="runtime-location-a",
        interaction_epoch=1,
        kind="device_location",
        display={
            "purpose": "nearby",
            "precision": "block",
            "address_required": True,
        },
        resolution_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["available", "disabled", "unavailable"],
                },
                "location": {"type": "string", "maxLength": 500},
                "precision": {
                    "type": "string",
                    "enum": ["city", "block", "precise"],
                },
            },
            "required": ["status", "location", "precision"],
            "additionalProperties": False,
        },
    )
    handle = await service.for_owner("conversation_turn").begin(
        RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        "turn-a",
        event,
    )

    interaction, resolved = await service.resolve(
        "principal-a",
        "interaction-location",
        {
            "status": "available",
            "location": "上海市浦东新区",
            "precision": "block",
        },
        resolved_by="mobile-app",
    )

    assert resolved is True
    assert interaction.kind == "device_location"
    assert await handle.wait() == {
        "status": "available",
        "location": "上海市浦东新区",
        "precision": "block",
    }


@pytest.mark.asyncio
async def test_expiring_an_interaction_closes_the_waiter_and_persists_state(
    tmp_path: Path,
) -> None:
    repository = HumanInteractionRepository(
        tmp_path / "platform.db",
        id_factory=lambda: "interaction-location",
    )
    service = HumanInteractionService(repository)
    handle = await service.for_owner("conversation_turn").begin(
        RuntimeScope(principal_id="principal-a", session_handle="session-a"),
        "turn-a",
        InteractionRequested(
            runtime_session_ref="runtime-session-a",
            runtime_turn_ref="runtime-turn-a",
            occurred_at=1.0,
            interaction_id="runtime-location-a",
            interaction_epoch=1,
            kind="device_location",
            display={},
            resolution_schema={"type": "object"},
        ),
    )

    await handle.expire()

    with pytest.raises(asyncio.CancelledError):
        await handle.wait()
    interaction = (
        await service.list_owner(
            "principal-a",
            "conversation_turn",
            "turn-a",
        )
    )[0]
    assert interaction.state == "expired"
    assert interaction.resolved_by == "timeout"
