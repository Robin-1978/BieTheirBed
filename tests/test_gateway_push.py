from __future__ import annotations

from pc_assistant.gateway.push import (
    GatewayPushDispatcher,
    GatewayPushRepository,
)
from pc_assistant.tasks import (
    PrincipalTaskEvent,
    TaskEvent,
    TaskEventPayload,
)


class _Transport:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, registration, message) -> None:
        self.sent.append((registration, message))


def _feed(event_type: str, *, approval_id: str = "") -> PrincipalTaskEvent:
    return PrincipalTaskEvent(
        feed_event_id=4,
        principal_id="personal:owner",
        event=TaskEvent(
            task_id="task-a",
            event_seq=3,
            event_type=event_type,
            occurred_at=1.0,
            payload=TaskEventPayload(approval_id=approval_id),
        ),
    )


def test_gateway_push_repository_replaces_only_current_device_registration(
    tmp_path,
) -> None:
    repository = GatewayPushRepository(tmp_path / "data" / "gateway.db")
    first = repository.register(
        "dev-a",
        "personal:owner",
        "expo",
        "ExponentPushToken[first-token]",
    )
    second = repository.register(
        "dev-a",
        "personal:owner",
        "expo",
        "ExponentPushToken[second-token]",
    )

    registrations = repository.list_for_principal("personal:owner")

    assert len(registrations) == 1
    assert registrations[0].token == "ExponentPushToken[second-token]"
    assert second.created_at == first.created_at
    assert repository.unregister("personal:other", "dev-a") is False
    assert repository.unregister("personal:owner", "dev-a") is True


async def test_gateway_push_dispatcher_maps_only_actionable_standard_events(
    tmp_path,
) -> None:
    repository = GatewayPushRepository(tmp_path / "data" / "gateway.db")
    repository.register(
        "dev-a",
        "personal:owner",
        "expo",
        "ExponentPushToken[token-a]",
    )
    transport = _Transport()
    dispatcher = GatewayPushDispatcher(
        "personal:owner",
        object(),
        repository,
        transport,
    )

    await dispatcher._deliver(_feed("reasoning_delta"))
    await dispatcher._deliver(_feed("approval_requested", approval_id="approval-a"))
    await dispatcher._deliver(_feed("completed"))

    assert [message.category for _registration, message in transport.sent] == [
        "approval",
        "task_completed",
    ]
    assert transport.sent[0][1].approval_id == "approval-a"
    assert transport.sent[0][1].task_id == "task-a"


def test_gateway_push_cursor_is_monotonic(tmp_path) -> None:
    repository = GatewayPushRepository(tmp_path / "data" / "gateway.db")

    repository.save_cursor("personal:owner", 8)
    repository.save_cursor("personal:owner", 3)

    assert repository.cursor("personal:owner") == 8
