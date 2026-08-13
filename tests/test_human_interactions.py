from __future__ import annotations

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
    assert repository.list_owner(
        "principal-a", "conversation_turn", "turn-a"
    ) == (interaction,)
    assert changed == [
        ("conversation_turn", "turn-a", "pending"),
        ("conversation_turn", "turn-a", "resolved"),
    ]
