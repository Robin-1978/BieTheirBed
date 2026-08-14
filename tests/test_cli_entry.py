from __future__ import annotations

import runpy
from types import SimpleNamespace

import pytest

import knoa_platform
from knoa_platform import __version__, _is_knoa_service_pid, async_main, main


@pytest.mark.asyncio
async def test_async_main_uses_textual_async_runner(monkeypatch) -> None:
    calls: list[str] = []

    class Config:
        @staticmethod
        def resolve_model():
            return SimpleNamespace(
                driver="http",
                provider_name="test",
                api_key="",
            )

    class Client:
        async def health(self):
            return SimpleNamespace(healthy=True, detail="")

        async def create_session(self):
            return "session-a"

        async def disconnect(self):
            calls.append("disconnect")

    class ChatApp:
        def __init__(self, _config, _client, session_handle):
            assert session_handle == "session-a"

        async def run_async(self):
            calls.append("run_async")

        def run(self):
            raise AssertionError("Textual sync runner must not be used")

    async def get_client(_config):
        return Client()

    monkeypatch.setattr("knoa_platform.config.load_config", lambda _path: Config())
    monkeypatch.setattr(
        "knoa_platform.service.core_lifecycle.get_core_client",
        get_client,
    )
    monkeypatch.setattr("knoa_platform.ui.core_app.CoreChatApp", ChatApp)

    assert await async_main(None, False) == 0
    assert calls == ["run_async", "disconnect"]


def test_version_uses_product_name_and_platform_version(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"Knoa {__version__}\n"


def test_python_module_entry_preserves_cli_exit_code(monkeypatch) -> None:
    monkeypatch.setattr(knoa_platform, "main", lambda: 7)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("knoa_platform.__main__", run_name="__main__")

    assert exc_info.value.code == 7


def test_parser_exposes_generic_task_and_interaction_commands() -> None:
    parser = knoa_platform.build_parser()
    assert parser.parse_args(["tasks", "--limit", "5"]).command == "tasks"
    task_state = parser.parse_args(["task-state", "task-a", "archived"])
    assert task_state.state == "archived"
    assert parser.parse_args(["task-delete", "task-a"]).task_id == "task-a"
    cancel = parser.parse_args(["execution-cancel", "execution-a", "--reason", "old"])
    assert cancel.reason == "old"
    resolved = parser.parse_args(["resolve", "interaction-a", '{"action":"decline"}'])
    assert resolved.interaction_id == "interaction-a"
    assert parser.parse_args(["follow-up", "task-a", "continue"]).command == "follow-up"
    deployment = parser.parse_args(
        ["mcp-package-deploy", "/workspace/jira", "jira"]
    )
    assert deployment.command == "mcp-package-deploy"
    assert deployment.server_id == "jira"
    event = parser.parse_args(
        [
            "task-create-event",
            "jira",
            "jira://assigned-to-me/events",
            "Analyze assignments",
            "--descendants-only",
        ]
    )
    assert event.descendants_only is True


def test_mcp_package_deploy_dispatches_without_duplicate_config(
    monkeypatch,
) -> None:
    calls = []

    async def run_client_command(config, command, **values):
        calls.append((config, command, values))
        return 0

    config = SimpleNamespace()
    monkeypatch.setattr("knoa_platform.config.load_config", lambda _path: config)
    monkeypatch.setattr(
        "knoa_platform.cli_management.run_client_command",
        run_client_command,
    )

    assert main(["mcp-package-deploy", "/workspace/jira", "jira"]) == 0
    assert calls[0][0] is config
    assert calls[0][1] == "mcp-package-deploy"
    assert "config" not in calls[0][2]


def test_start_uses_authoritative_service_lifecycle(monkeypatch) -> None:
    calls: list[tuple[str | None, str | None]] = []

    def start(config_path: str | None, log_dir: str | None) -> int:
        calls.append((config_path, log_dir))
        return 0

    monkeypatch.setattr(knoa_platform, "_start_service", start)

    assert main(["--start", "--log-dir", "/tmp/knoa-test-logs"]) == 0
    assert calls == [(None, "/tmp/knoa-test-logs")]


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        (b"python\0-m\0knoa_platform\0--serve\0--daemon\0", True),
        (b"python\0-m\0knoa_platform.service\0--daemon\0", True),
        (b"python\0-m\0unrelated_server\0--serve\0", False),
        (b"python\0server.py\09527\0", False),
    ],
)
def test_service_pid_identity_requires_knoa_module(
    monkeypatch,
    cmdline: bytes,
    expected: bool,
) -> None:
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda _self: cmdline)
    assert _is_knoa_service_pid(1234) is expected
