from __future__ import annotations

from pathlib import Path

import pytest

from pc_assistant.agent_runtime.contracts import ConfigSetRequest, ConfigSetResult, RuntimeScope
from pc_assistant.agent_runtime.control import ControlService
from pc_assistant.agent_runtime.session_store import RuntimeSessionRepository, SessionSnapshot
from pc_assistant.context.memory_db import SQLiteMemoryRepository
from pc_assistant.exceptions import SessionNotFoundError


class FakeConfigController:
    def __init__(self) -> None:
        self.requests: list[ConfigSetRequest] = []

    async def set_config(self, request: ConfigSetRequest) -> ConfigSetResult:
        self.requests.append(request)
        return ConfigSetResult(applied=True)


def _service(tmp_path: Path, handles: tuple[str, ...] = ("session-a", "session-b")):
    handle_iter = iter(handles)
    sessions = RuntimeSessionRepository(
        tmp_path / "assistant.db",
        handle_factory=lambda: next(handle_iter),
    )
    memory = SQLiteMemoryRepository(tmp_path / "assistant.db")
    config = FakeConfigController()
    service = ControlService(
        sessions,
        memory,
        tool_names=lambda _scope: ["write_file", "read_file", "read_file"],
        config_controller=config,
        status_details=lambda _scope: {
            "model": "model-a",
            "prompt_tokens": 120,
            "completion_tokens": 30,
        },
    )
    return service, sessions, memory, config


@pytest.mark.asyncio
async def test_control_service_creates_core_owned_session(tmp_path: Path) -> None:
    service, sessions, _memory, _config = _service(tmp_path)

    scope = await service.create_session("principal-a")

    assert scope == RuntimeScope(principal_id="principal-a", session_handle="session-a")
    assert sessions.active("principal-a") == scope


@pytest.mark.asyncio
async def test_history_and_status_require_owned_scope(tmp_path: Path) -> None:
    service, sessions, _memory, _config = _service(tmp_path)
    scope = await service.create_session("principal-a")
    sessions.save(
        scope,
        SessionSnapshot(messages=({"role": "user", "content": "hello"},)),
    )

    history = await service.get_history(scope)
    status = await service.get_status(scope)

    assert history.messages[0]["content"] == "hello"
    assert status.details == {
        "model": "model-a",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "session_handle": "session-a",
        "messages": 1,
        "sessions": 1,
        "available_tools": 2,
    }
    foreign = RuntimeScope(principal_id="principal-b", session_handle=scope.session_handle)
    with pytest.raises(SessionNotFoundError):
        await service.get_history(foreign)
    with pytest.raises(SessionNotFoundError):
        await service.get_status(foreign)


@pytest.mark.asyncio
async def test_memory_is_scoped_and_clear_does_not_cross_principals(tmp_path: Path) -> None:
    service, _sessions, memory, _config = _service(tmp_path)
    scope_a = await service.create_session("principal-a")
    scope_b = await service.create_session("principal-b")
    memory.store_memory(
        "principal-a",
        "city",
        "Shanghai",
        category="preference",
        importance="relevant",
        confidence=1.0,
        source="explicit",
    )
    memory.store_memory(
        "principal-b",
        "city",
        "Beijing",
        category="preference",
        importance="relevant",
        confidence=1.0,
        source="explicit",
    )

    listed = await service.list_memory(scope_a)
    await service.clear_memory(scope_a)

    assert [(item.key, item.value) for item in listed.memories] == [("city", "Shanghai")]
    assert memory.list_memories("principal-a") == []
    assert memory.list_memories("principal-b")[0]["value"] == "Beijing"
    assert (await service.list_memory(scope_b)).memories[0].value == "Beijing"


@pytest.mark.asyncio
async def test_tools_are_deduplicated_and_require_owned_scope(tmp_path: Path) -> None:
    service, _sessions, _memory, _config = _service(tmp_path)
    scope = await service.create_session("principal-a")

    result = await service.list_tools(scope)

    assert result.tools == ("read_file", "write_file")
    with pytest.raises(SessionNotFoundError):
        await service.list_tools(
            RuntimeScope(principal_id="principal-b", session_handle=scope.session_handle)
        )


@pytest.mark.asyncio
async def test_config_is_local_admin_only_and_uses_typed_request(tmp_path: Path) -> None:
    service, _sessions, _memory, config = _service(tmp_path)
    local = await service.create_session("local")
    remote = await service.create_session("principal-b")
    request = ConfigSetRequest(field_name="max_iterations", value=12)

    result = await service.set_config(local, request)

    assert result.applied
    assert config.requests == [request]
    with pytest.raises(PermissionError, match="capability denied"):
        await service.set_config(remote, request)
    assert config.requests == [request]
