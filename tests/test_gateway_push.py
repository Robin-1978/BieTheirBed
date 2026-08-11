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


class _Core:
    def __init__(self, notification_policy=None) -> None:
        self.notification_policy = notification_policy or {}

    async def get_product_task_execution(self, principal_id, execution_id):
        assert principal_id == "personal:owner"
        assert execution_id == "task-a"
        return type("Execution", (), {"task_id": "stable-task-a"})()

    async def get_product_task(self, principal_id, task_id):
        assert principal_id == "personal:owner"
        assert task_id == "stable-task-a"
        return type("Task", (), {"notification_policy": self.notification_policy})()


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
    assert repository.get_for_device("personal:owner", "dev-a") == second
    assert repository.get_for_device("personal:other", "dev-a") is None
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
        _Core(),
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
    assert transport.sent[0][1].task_id == "stable-task-a"
    assert transport.sent[0][1].execution_id == "task-a"


async def test_gateway_push_dispatcher_honors_task_notification_policy(tmp_path) -> None:
    repository = GatewayPushRepository(tmp_path / "data" / "gateway.db")
    repository.register("dev-a", "personal:owner", "expo", "ExponentPushToken[token-a]")
    transport = _Transport()
    dispatcher = GatewayPushDispatcher(
        "personal:owner",
        _Core({"completed": False, "failed": True}),
        repository,
        transport,
    )

    await dispatcher._deliver(_feed("completed"))
    await dispatcher._deliver(_feed("failed"))

    assert [message.category for _registration, message in transport.sent] == ["task_failed"]


async def test_gateway_push_dispatcher_sends_test_to_current_device(tmp_path) -> None:
    repository = GatewayPushRepository(tmp_path / "data" / "gateway.db")
    repository.register("dev-a", "personal:owner", "expo", "ExponentPushToken[token-a]")
    transport = _Transport()
    dispatcher = GatewayPushDispatcher(
        "personal:owner",
        _Core(),
        repository,
        transport,
    )

    assert await dispatcher.send_test("personal:owner", "dev-a") is True
    assert await dispatcher.send_test("personal:owner", "dev-b") is False
    assert len(transport.sent) == 1
    assert transport.sent[0][1].category == "test"


def test_gateway_push_cursor_is_monotonic(tmp_path) -> None:
    repository = GatewayPushRepository(tmp_path / "data" / "gateway.db")

    repository.save_cursor("personal:owner", 8)
    repository.save_cursor("personal:owner", 3)

    assert repository.cursor("personal:owner") == 8
