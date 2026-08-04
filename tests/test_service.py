"""Tests for the service layer: protocol, server, client, lifecycle."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pc_assistant.agent import AgentEvent
from pc_assistant.service.protocol import (
    ClientMessage,
    ServerMessage,
    serialize,
    deserialize_client,
)
from pc_assistant.service.agent_like import AgentLike


# ── Protocol tests ────────────────────────────────────────────────────


class TestClientMessage:
    def test_create_run(self):
        msg = ClientMessage(method="run", id=1, params={"input": "hello", "session_id": ""})
        assert msg.is_run()
        assert msg.input_text == "hello"
        assert msg.session_id == ""

    def test_create_cancel(self):
        msg = ClientMessage(method="cancel", id=2, params={"session_id": "ws:abc"})
        assert msg.is_cancel()
        assert msg.session_id == "ws:abc"

    def test_create_confirm(self):
        msg = ClientMessage(method="confirm", id=3, params={"code": "abc", "approved": True})
        assert msg.is_confirm()
        assert msg.params["approved"] is True

    def test_json_roundtrip(self):
        msg = ClientMessage(method="run", id=1, params={"input": "hi"})
        raw = msg.model_dump_json()
        restored = ClientMessage.model_validate_json(raw)
        assert restored.method == "run"
        assert restored.input_text == "hi"


class TestServerMessage:
    def test_event_factory(self):
        event_data = {"type": "stream_delta", "content": "hello"}
        msg = ServerMessage.event(1, event_data)
        assert msg.type == "event"
        assert msg.run_id == 1
        assert msg.data["content"] == "hello"

    def test_result_factory(self):
        msg = ServerMessage.result(5, {"done": True})
        assert msg.type == "result"
        assert msg.id == 5
        assert msg.data["done"] is True

    def test_error_factory(self):
        msg = ServerMessage.error(3, "something failed")
        assert msg.type == "error"
        assert msg.data["message"] == "something failed"

    def test_confirm_request_factory(self):
        msg = ServerMessage.confirm_request("shell", {"command": "rm -rf"}, "abc123")
        assert msg.type == "confirm_request"
        assert msg.data["tool"] == "shell"
        assert msg.data["code"] == "abc123"

    def test_notify_factory(self):
        msg = ServerMessage.notify("task_1", "Timer fired!")
        assert msg.type == "notify"
        assert msg.data["task_id"] == "task_1"

    def test_serialize(self):
        msg = ServerMessage.result(1, {"key": "value"})
        raw = serialize(msg)
        data = json.loads(raw)
        assert data["type"] == "result"
        assert data["id"] == 1

    def test_deserialize_client(self):
        raw = '{"method": "status", "id": 7}'
        msg = deserialize_client(raw)
        assert msg.method == "status"
        assert msg.id == 7


# ── AgentLike protocol tests ─────────────────────────────────────────


class TestAgentLikeProtocol:
    def test_agent_satisfies_protocol(self):
        from pc_assistant.agent import Agent
        from pc_assistant.config import AppConfig

        cfg = AppConfig(llm_provider="llamacpp")
        agent = Agent(config=cfg)
        assert isinstance(agent, AgentLike)

    def test_service_client_satisfies_protocol(self):
        from pc_assistant.service.client import ServiceClient

        client = ServiceClient()
        assert isinstance(client, AgentLike)


# ── Server confirm futures (scoped + fail-closed) ────────────────────


class TestServerConfirmScoping:
    """Confirm futures are scoped by client and always resolve (fail closed)."""

    def _make_server(self):
        from pc_assistant.config import AppConfig
        from pc_assistant.service.server import ServiceServer

        server = ServiceServer(AppConfig(llm_provider="llamacpp"))
        server._agent = MagicMock()
        return server

    def _pending_future(self, server, client_id, code, session_id="session-x"):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        server._confirm_futures[(client_id, code)] = (session_id, future)
        return future

    @pytest.mark.asyncio
    async def test_confirm_scoped_by_client(self):
        server = self._make_server()
        f_a = self._pending_future(server, "clientA", "code1")
        f_b = self._pending_future(server, "clientB", "code1")

        server._handle_confirm(MagicMock(), "clientB", ClientMessage(
            method="confirm", id=3, params={"code": "code1", "approved": True}
        ))

        assert f_a.done() is False, "Other client's future must not be touched"
        assert f_b.result() is True

    @pytest.mark.asyncio
    async def test_client_disconnect_resolves_pending_futures_fail_closed(self):
        server = self._make_server()
        f_a = self._pending_future(server, "clientA", "code1")
        self._pending_future(server, "clientB", "code2")

        server._resolve_client_confirm_futures("clientA", approved=False)

        assert f_a.done() and f_a.result() is False, "Disconnect must deny"
        assert ("clientB", "code2") in server._confirm_futures, (
            "Other client's futures must survive"
        )

    @pytest.mark.asyncio
    async def test_cancel_scoped_by_session(self):
        server = self._make_server()
        f_target = self._pending_future(server, "clientA", "code1", session_id="feishu:u1")
        f_other = self._pending_future(server, "clientB", "code2", session_id="feishu:u2")

        server._handle_cancel(ClientMessage(
            method="cancel", id=4, params={"session_id": "feishu:u1"}
        ))

        assert f_target.result() is False, "Cancelled session must fail closed"
        assert f_other.done() is False, "Other session's confirm must survive"
        assert ("clientA", "code1") not in server._confirm_futures

    @pytest.mark.asyncio
    async def test_cancel_without_session_resolves_all_pending_futures(self):
        server = self._make_server()
        f_a = self._pending_future(server, "clientA", "code1")
        f_b = self._pending_future(server, "clientB", "code2")

        server._handle_cancel(ClientMessage(method="cancel", id=4))

        assert f_a.result() is False and f_b.result() is False
        assert server._confirm_futures == {}


class TestServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_failure_cancels_attachment_cleanup_task(self, tmp_path):
        from pc_assistant.config import AppConfig
        from pc_assistant.service.server import ServiceServer

        cfg = AppConfig(
            llm_provider="llamacpp",
            llm_server_url="http://127.0.0.1:1",
            runtime_root=str(tmp_path),
        )
        server = ServiceServer(cfg)
        fake_agent = MagicMock()
        fake_agent.health_check = AsyncMock(return_value=True)
        fake_agent.registry.get = MagicMock(return_value=None)

        unix_server = MagicMock()
        unix_server.close = MagicMock()
        unix_server.wait_closed = AsyncMock()

        with (
            patch("pc_assistant.service.server.Agent", return_value=fake_agent),
            patch("pc_assistant.service.server.SOCKET_PATH", tmp_path / "service.sock"),
            patch("pc_assistant.service.server.PID_PATH", tmp_path / "service.pid"),
            patch("pc_assistant.service.server.websockets.unix_serve", AsyncMock(return_value=unix_server)),
            patch("pc_assistant.service.server._write_pid", side_effect=OSError("pid write failed")),
        ):
            with pytest.raises(OSError, match="pid write failed"):
                await server.start()

        assert server._attachment_cleanup_task is None
        unix_server.close.assert_called_once()
        unix_server.wait_closed.assert_awaited_once()

    def test_service_log_uses_configured_runtime_root(self, tmp_path, monkeypatch):
        from pc_assistant.service.server import resolve_service_log

        monkeypatch.delenv("PC_RUNTIME_ROOT", raising=False)
        monkeypatch.delenv("PC_ASSISTANT_HOME", raising=False)
        config_path = tmp_path / "config.yaml"
        runtime_root = tmp_path / "runtime"
        config_path.write_text(f"runtime_root: {runtime_root}\n", encoding="utf-8")

        assert resolve_service_log(None, str(config_path)) == runtime_root / "logs" / "service.log"


# ── Server + Client integration ──────────────────────────────────────


class TestServerClientIntegration:
    """Start a real server on a temp socket, connect a client, exchange messages."""

    @pytest.fixture
    async def server_and_client(self, tmp_path):
        """Spin up server + client on a temp Unix socket."""
        import websockets
        from pc_assistant.service.server import ServiceServer
        from pc_assistant.service.client import ServiceClient
        from pc_assistant.config import AppConfig

        sock_path = tmp_path / "test.sock"

        cfg = AppConfig(llm_provider="llamacpp", llm_server_url="http://127.0.0.1:1")

        server = ServiceServer(cfg)

        mock_agent = AsyncMock()
        mock_agent.health_check = AsyncMock(return_value=True)
        mock_agent.get_status = AsyncMock(return_value={"status": "ok", "total_tokens": 0})
        mock_agent.session_stats = MagicMock(return_value=[])
        mock_agent.cancel = MagicMock()
        mock_agent.reset_conversation = MagicMock()
        mock_agent.drop_session = MagicMock()
        mock_agent.store_attachment = MagicMock(return_value={
            "type": "image_ref",
            "attachment_id": "uploaded-ref",
            "media_type": "image/jpeg",
        })
        mock_agent.conversation = MagicMock()
        mock_agent.conversation.clear = MagicMock()

        mock_registry = MagicMock()
        mock_registry.get = MagicMock(return_value=None)
        mock_registry.list_tools = MagicMock(return_value=["shell", "filesystem"])
        mock_agent.registry = mock_registry

        async def mock_run(text, *, session_id="", confirm_callback=None, attachments=None):
            mock_agent.last_run_attachments = attachments
            yield AgentEvent(type="stream_delta", content="Hello ")
            yield AgentEvent(type="stream_delta", content="world!")
            if confirm_callback is not None and text.startswith("delete"):
                approved = await confirm_callback("shell", {"command": "rm -rf /"})
                yield AgentEvent(type="final_answer", content=f"approved={approved}")
            else:
                yield AgentEvent(type="final_answer", content="Hello world!")

        mock_agent.run = mock_run
        server._agent = mock_agent

        ws_server = await websockets.unix_serve(
            server._handle_client,
            str(sock_path),
        )

        client = ServiceClient(socket_path=sock_path)
        await client.connect()

        yield server, client

        await client.disconnect()
        ws_server.close()
        await ws_server.wait_closed()

    async def test_health_check(self, server_and_client):
        server, client = server_and_client
        resp = await client._request("health")
        assert resp.data.get("healthy") is True

    async def test_status(self, server_and_client):
        server, client = server_and_client
        status = await client.get_status_async()
        assert "status" in status

    async def test_run_streaming(self, server_and_client):
        server, client = server_and_client
        events = []
        async for event in client.run("hello"):
            events.append(event)

        types = [e.type for e in events]
        assert "stream_delta" in types
        assert "final_answer" in types

        deltas = [e.content for e in events if e.type == "stream_delta"]
        assert "".join(deltas) == "Hello world!"

    async def test_attachment_upload_precedes_reference_only_run(self, server_and_client):
        from pc_assistant.model_adapter.types import ImageAttachment

        server, client = server_and_client
        events = []
        async for event in client.run(
            "look",
            session_id="image-session",
            attachments=[ImageAttachment(data_url="data:image/jpeg;base64,AAAA")],
        ):
            events.append(event)

        server._agent.store_attachment.assert_called_once()
        stored_session, _ = server._agent.store_attachment.call_args.args
        assert stored_session == "image-session"
        assert server._agent.last_run_attachments[0].attachment_id == "uploaded-ref"
        assert any(event.type == "final_answer" for event in events)

    async def test_confirm_reply_processed_during_run(self, server_and_client):
        """A confirm reply must be read while a run is in flight (no deadlock).

        Regression: the server used to block its read loop inside the run, so
        the client's ``confirm`` message sat unread until the 120s timeout.
        """
        server, client = server_and_client
        received_code = []

        async def handle_confirm(data):
            received_code.append(data.get("code", ""))
            await client.confirm(data["code"], True)

        client.set_confirm_handler(handle_confirm)

        events = []
        async for event in client.run("delete temp"):
            events.append(event)

        assert received_code, "Server must emit a confirm_request"
        final = [e for e in events if e.type == "final_answer"]
        assert final and final[0].content == "approved=True", (
            "Server must process the confirm reply mid-run"
        )

    async def test_confirm_deny_processed_during_run(self, server_and_client):
        server, client = server_and_client

        async def handle_confirm(data):
            await client.confirm(data["code"], False)

        client.set_confirm_handler(handle_confirm)

        events = []
        async for event in client.run("delete temp"):
            events.append(event)

        final = [e for e in events if e.type == "final_answer"]
        assert final and final[0].content == "approved=False"

    async def test_cancel(self, server_and_client):
        server, client = server_and_client
        await client.cancel()
        await asyncio.sleep(0.1)
        server._agent.cancel.assert_called()

    async def test_command_clear(self, server_and_client):
        server, client = server_and_client
        async for _ in client.run("hello", session_id="tui-session"):
            pass
        result = await client.command("/clear")
        assert result.get("cleared") is True
        assert result.get("session_id") == "tui-session"
        server._agent.drop_session.assert_called_once_with("tui-session")

    async def test_command_tools(self, server_and_client):
        server, client = server_and_client
        result = await client.command("/tools")
        assert "tools" in result
        assert "shell" in result["tools"]

    async def test_unknown_method(self, server_and_client):
        server, client = server_and_client
        resp = await client._request("nonexistent")
        assert resp.type == "error"

    async def test_notify_handler(self, server_and_client):
        server, client = server_and_client
        received = []
        client.set_notify_handler(lambda tid, msg: received.append((tid, msg)))

        server._on_timer_notify("task_1", "Timer done!")
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0] == ("task_1", "Timer done!")


# ── Lifecycle tests ───────────────────────────────────────────────────


class TestLifecycle:
    def test_cli_start_uses_dedicated_service_module(self, tmp_path):
        import sys
        import pc_assistant

        with (
            patch.object(pc_assistant, "_service_state", side_effect=[(False, None), (True, 1234)]),
            patch.object(pc_assistant, "_wait_for_running", return_value=True),
            patch("subprocess.Popen") as popen,
        ):
            result = pc_assistant._start_service(
                str(tmp_path / "config.yaml"),
                str(tmp_path / "logs"),
            )

        assert result == 0
        command = popen.call_args.args[0]
        assert command[:4] == [sys.executable, "-m", "pc_assistant.service", "--daemon"]
        assert "--serve" not in command
        assert "--config" in command

    def test_auto_start_uses_executable_service_module(self):
        from pc_assistant.config import AppConfig
        from pc_assistant.service import lifecycle

        with patch("pc_assistant.service.lifecycle.subprocess.Popen") as popen:
            assert lifecycle._start_daemon(AppConfig()) is True

        command = popen.call_args.args[0]
        assert command[1:4] == ["-m", "pc_assistant.service", "--daemon"]

    def test_is_running_no_pid(self, tmp_path):
        with patch("pc_assistant.service.server.PID_PATH", tmp_path / "nope.pid"):
            from pc_assistant.service.server import is_running
            assert is_running() is False

    def test_is_running_stale_pid(self, tmp_path):
        pid_file = tmp_path / "stale.pid"
        pid_file.write_text("999999999")
        with patch("pc_assistant.service.server.PID_PATH", pid_file):
            from pc_assistant.service.server import is_running
            assert is_running() is False
