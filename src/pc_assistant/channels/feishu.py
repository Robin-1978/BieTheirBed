"""Feishu adapter that speaks only the public Core WebSocket API."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pc_assistant.config import AppConfig
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_api import (
    ArtifactInputRef,
    ConfirmationRequestedMessage,
)
from pc_assistant.service.core_client import CoreClient, CoreRequestError
from pc_assistant.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)


logger = logging.getLogger(__name__)
_CONFIRM_TIMEOUT_SECONDS = 110.0

for _proxy_name in ("NO_PROXY", "no_proxy"):
    _configured = os.environ.get(_proxy_name, "")
    _hosts = [
        "msg-frontier.feishu.cn",
        "open.feishu.cn",
        "feishu.cn",
        "larkoffice.com",
        "127.0.0.1",
        "localhost",
    ]
    _entries = [entry for entry in _configured.split(",") if entry]
    for _host in _hosts:
        if _host not in _entries:
            _entries.append(_host)
    os.environ[_proxy_name] = ",".join(_entries)


def _principal_for_log(open_id: str) -> str:
    if not open_id:
        return "unknown"
    return hashlib.sha256(open_id.encode("utf-8")).hexdigest()[:10]


class FeishuChannel:
    """Translate Feishu ingress/egress at the CoreClient boundary."""

    name = "feishu"

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._paths = RuntimePaths.from_root(config.runtime_root)
        self._app_id = config.feishu_app_id.strip()
        self._app_secret = config.feishu_app_secret.get_secret_value().strip()
        self._receive_id = config.feishu_receive_id.strip()
        self._binding_path = self._paths.data / "feishu_open_id"
        self._sessions_path = self._paths.data / "feishu_sessions.json"
        self._outbox = self._paths.cache / "feishu-outbox"
        self._clients: dict[str, CoreClient] = {}
        self._lark_client: Any = None
        self._lark_lock = threading.RLock()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._running = False
        self._sessions: dict[str, str] = {}
        self._session_users: dict[str, str] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._pending_attachments: dict[str, list[ArtifactInputRef]] = {}
        self._pending_confirmations: dict[str, asyncio.Future[bool]] = {}
        self._seen_messages: dict[str, float] = {}

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("FeishuChannel is already started")
        if not self._app_id or not self._app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        self._main_loop = asyncio.get_running_loop()
        self._load_sessions()
        # Import and initialize the SDK before the WebSocket thread starts.
        # Concurrent first imports from REST and WS paths can deadlock inside
        # Python's module locks in lark-oapi 1.6.x.
        await asyncio.to_thread(self._get_lark_client)
        self._running = True
        self._ws_thread = threading.Thread(
            target=self._run_websocket,
            name="pc-assistant-feishu",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("FeishuChannel started")
        receive_id = self._current_receive_id()
        if receive_id:
            await asyncio.to_thread(
                self._send_text,
                receive_id,
                "🟢 PC Assistant 已连接到新 Core 服务",
            )

    async def stop(self) -> None:
        self._running = False
        for future in tuple(self._pending_confirmations.values()):
            if not future.done():
                future.set_result(False)
        self._pending_confirmations.clear()
        clients, self._clients = tuple(self._clients.values()), {}
        await asyncio.gather(
            *(client.disconnect() for client in clients),
            return_exceptions=True,
        )
        logger.info("FeishuChannel stopped")

    @property
    def running(self) -> bool:
        return self._running

    def _run_websocket(self) -> None:
        while self._running:
            event_loop: asyncio.AbstractEventLoop | None = None
            try:
                import lark_oapi.ws.client as ws_module
                from lark_oapi.ws import Client as WSClient

                event_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(event_loop)
                ws_module.loop = event_loop
                self._lark_ws_client = WSClient(
                    app_id=self._app_id,
                    app_secret=self._app_secret,
                    event_handler=self._create_event_handler(),
                    auto_reconnect=True,
                )
                logging.getLogger("Lark").setLevel(logging.WARNING)
                logger.info("Feishu WebSocket connecting")
                self._lark_ws_client.start()
            except Exception:
                logger.exception("Feishu WebSocket connection failed")
            finally:
                if event_loop is not None:
                    try:
                        pending = asyncio.all_tasks(event_loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            event_loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                        event_loop.close()
                    except Exception:
                        logger.debug("Feishu WebSocket loop cleanup failed", exc_info=True)
            if self._running:
                time.sleep(3)

    def _create_event_handler(self) -> Any:
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

        def on_message(event: Any) -> None:
            try:
                sender = event.event.sender
                open_id = sender.sender_id.open_id
                message = event.event.message
                message_id = getattr(message, "message_id", "") or ""
                if not self._claim_message(message_id):
                    return
                content = json.loads(message.content or "{}")
                if message.message_type == "text":
                    text = str(content.get("text", "")).strip()
                    if text:
                        self._submit(self._handle_text(open_id, text))
                elif message.message_type == "image":
                    image_key = str(content.get("image_key", "")).strip()
                    if image_key:
                        self._submit(
                            self._handle_image(open_id, message_id, image_key)
                        )
            except Exception:
                logger.exception("Feishu inbound message handling failed")

        def on_activity(_event: Any) -> None:
            return None

        return (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .register_p2_im_message_message_read_v1(on_activity)
            .register_p2_im_message_reaction_created_v1(on_activity)
            .register_p2_im_message_reaction_deleted_v1(on_activity)
            .build()
        )

    def _submit(self, coroutine: Any) -> None:
        loop = self._main_loop
        if loop is None or not self._running:
            coroutine.close()
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)

        def report_failure(done: Any) -> None:
            try:
                done.result()
            except Exception:
                logger.exception("Feishu scheduled operation failed")

        future.add_done_callback(report_failure)

    async def _handle_text(self, open_id: str, text: str) -> None:
        self._save_binding(open_id)
        normalized = text.strip().lower()
        confirmation = self._pending_confirmations.get(open_id)
        if confirmation is not None and not confirmation.done():
            if normalized in {"确认", "批准", "yes", "y", "ok"}:
                confirmation.set_result(True)
                await asyncio.to_thread(self._send_text, open_id, "✅ 已批准执行")
                return
            if normalized in {"取消", "拒绝", "no", "n"}:
                confirmation.set_result(False)
                await asyncio.to_thread(self._send_text, open_id, "❌ 已拒绝执行")
                return
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "当前有操作等待确认，请回复“确认”或“取消”。",
            )
            return

        lock = self._user_locks.setdefault(open_id, asyncio.Lock())
        async with lock:
            try:
                await self._run_text(open_id, text)
            except Exception as exc:
                logger.exception(
                    "Feishu Core run failed principal=%s",
                    _principal_for_log(open_id),
                )
                await asyncio.to_thread(
                    self._send_text,
                    open_id,
                    f"❌ 处理失败：{type(exc).__name__}",
                )

    async def _run_text(self, open_id: str, text: str) -> None:
        client = await self._client_for(open_id)
        if text.strip().lower() == "/new":
            session = await client.create_session()
            self._bind_session(open_id, session)
            await asyncio.to_thread(self._send_text, open_id, "✅ 已创建新会话")
            return

        session = await self._session_for(open_id)
        if text.strip().lower() == "/status":
            status = await client.status(session)
            await asyncio.to_thread(
                self._send_text,
                open_id,
                f"Core: {status.status}\nConnected: {status.connected}",
            )
            return
        if text.strip().lower() == "/tools":
            tools = await client.list_tools(session)
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "可用工具：\n" + "、".join(tools.tools),
            )
            return

        attachments = tuple(self._pending_attachments.pop(open_id, []))
        await asyncio.to_thread(self._send_text, open_id, "⏳ 正在处理...")
        answer: list[str] = []
        artifacts: list[str] = []
        failed = ""
        try:
            async for event in client.run(session, text, attachments):
                if event.event_type == "content_delta":
                    answer.append(event.payload.content)
                elif event.event_type == "artifact" and event.payload.artifact:
                    artifacts.append(event.payload.artifact.artifact_id)
                elif event.event_type in {"failed", "cancelled"}:
                    failed = event.payload.content or event.event_type
        except CoreRequestError as exc:
            if exc.code != "session_not_found":
                raise
            session = await client.create_session()
            self._bind_session(open_id, session)
            async for event in client.run(session, text, attachments):
                if event.event_type == "content_delta":
                    answer.append(event.payload.content)
                elif event.event_type == "artifact" and event.payload.artifact:
                    artifacts.append(event.payload.artifact.artifact_id)
                elif event.event_type in {"failed", "cancelled"}:
                    failed = event.payload.content or event.event_type

        response = "".join(answer).strip()
        if failed:
            response = f"❌ {failed}"
        if not response:
            response = "✅ 已完成"
        await asyncio.to_thread(self._send_card, open_id, response)
        for artifact_id in artifacts[:5]:
            await self._deliver_artifact(open_id, session, artifact_id)

    async def _handle_image(
        self,
        open_id: str,
        message_id: str,
        image_key: str,
    ) -> None:
        self._save_binding(open_id)
        try:
            data, media_type = await asyncio.to_thread(
                self._download_image,
                message_id,
                image_key,
            )
            session = await self._session_for(open_id)
            encoded = base64.b64encode(data).decode("ascii")
            client = await self._client_for(open_id)
            artifact = await client.upload_artifact(
                session,
                f"data:{media_type};base64,{encoded}",
                media_type=media_type,
                caption="Feishu image",
            )
            pending = self._pending_attachments.setdefault(open_id, [])
            pending.append(
                ArtifactInputRef(
                    artifact_id=artifact.artifact_id,
                    caption="Feishu image",
                )
            )
            self._pending_attachments[open_id] = pending[-4:]
            await asyncio.to_thread(
                self._send_text,
                open_id,
                "🖼️ 图片已收到，请继续发送问题。",
            )
        except Exception:
            logger.exception(
                "Feishu image ingress failed principal=%s",
                _principal_for_log(open_id),
            )
            await asyncio.to_thread(self._send_text, open_id, "❌ 图片接收失败")

    async def _confirm_tool(
        self,
        open_id: str,
        message: ConfirmationRequestedMessage,
    ) -> bool:
        if self._session_users.get(message.session_handle) != open_id:
            return False
        current = self._pending_confirmations.get(open_id)
        if current is not None and not current.done():
            return False
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending_confirmations[open_id] = future
        arguments = json.dumps(message.arguments, ensure_ascii=False, default=str)[:500]
        prompt = (
            f"⚠️ 工具 `{message.tool_name}` 请求执行\n"
            f"原因：{message.reason or '该操作可能改变系统状态'}\n"
            f"参数：{arguments}\n\n请回复“确认”或“取消”。"
        )
        await asyncio.to_thread(self._send_card, open_id, prompt, "orange", "操作确认")
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_confirmations.pop(open_id, None)

    async def _session_for(self, open_id: str) -> str:
        session = self._sessions.get(open_id)
        if session:
            self._session_users[session] = open_id
            return session
        session = await (await self._client_for(open_id)).create_session()
        self._bind_session(open_id, session)
        return session

    def _bind_session(self, open_id: str, session: str) -> None:
        previous = self._sessions.get(open_id)
        if previous:
            self._session_users.pop(previous, None)
        self._sessions[open_id] = session
        self._session_users[session] = open_id
        self._save_sessions()

    def _load_sessions(self) -> None:
        try:
            data = json.loads(self._sessions_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._sessions = {
                    str(key): str(value)
                    for key, value in data.items()
                    if str(key) and str(value)
                }
                self._session_users = {
                    session: open_id for open_id, session in self._sessions.items()
                }
        except FileNotFoundError:
            return
        except Exception:
            logger.warning("Ignoring invalid Feishu session mapping", exc_info=True)

    def _save_sessions(self) -> None:
        self._sessions_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._sessions_path.parent.chmod(0o700)
        temporary = self._sessions_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(self._sessions, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self._sessions_path)
        self._sessions_path.chmod(0o600)

    def _claim_message(self, message_id: str) -> bool:
        if not message_id:
            return True
        now = time.time()
        if message_id in self._seen_messages:
            return False
        self._seen_messages[message_id] = now
        if len(self._seen_messages) > 1000:
            self._seen_messages = {
                key: seen_at
                for key, seen_at in self._seen_messages.items()
                if now - seen_at < 600
            }
        return True

    def _save_binding(self, open_id: str) -> None:
        if not open_id:
            return
        self._receive_id = open_id
        self._binding_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._binding_path.write_text(open_id, encoding="utf-8")
        self._binding_path.chmod(0o600)

    def _current_receive_id(self) -> str:
        if self._receive_id:
            return self._receive_id
        try:
            return self._binding_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _get_lark_client(self) -> Any:
        if self._lark_client is None:
            with self._lark_lock:
                if self._lark_client is None:
                    from lark_oapi import Client

                    self._lark_client = (
                        Client.builder()
                        .app_id(self._app_id)
                        .app_secret(self._app_secret)
                        .build()
                    )
        return self._lark_client

    def _send_text(self, open_id: str, text: str) -> bool:
        from lark_oapi.api.im.v1 import CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message.create(request)
        if response.code == 0:
            logger.info(
                "Feishu text sent principal=%s chars=%d",
                _principal_for_log(open_id),
                len(text),
            )
            return True
        logger.error("Feishu text send failed code=%s msg=%s", response.code, response.msg)
        return False

    def _send_card(
        self,
        open_id: str,
        text: str,
        template: str = "blue",
        title: str = "PC Assistant",
    ) -> bool:
        from lark_oapi.api.im.v1 import CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        card = {
            "schema": "2.0",
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": text[:12000]}
                ]
            },
        }
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message.create(request)
        if response.code == 0:
            logger.info(
                "Feishu card sent principal=%s chars=%d",
                _principal_for_log(open_id),
                len(text),
            )
            return True
        logger.error("Feishu card send failed code=%s msg=%s", response.code, response.msg)
        return False

    def _download_image(self, message_id: str, image_key: str) -> tuple[bytes, str]:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message_resource.get(request)
        if not response.success() or not response.file:
            raise RuntimeError(
                f"Feishu image download failed: {response.code} {response.msg}"
            )
        data = response.file.read()
        media_type = "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            media_type = "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            media_type = "image/webp"
        return data, media_type

    async def _deliver_artifact(
        self,
        open_id: str,
        session: str,
        artifact_id: str,
    ) -> None:
        downloaded = await (await self._client_for(open_id)).download_artifact(
            session,
            artifact_id,
        )
        _header, encoded = downloaded.data_url.split(",", 1)
        data = base64.b64decode(encoded, validate=True)
        suffix = Path(downloaded.artifact.name).suffix
        if not suffix:
            suffix = mimetypes.guess_extension(downloaded.artifact.media_type) or ".bin"
        self._outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._outbox / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        target.chmod(0o600)
        try:
            sender = self._send_image if downloaded.artifact.kind == "image" else self._send_file
            await asyncio.to_thread(
                sender,
                open_id,
                target,
                downloaded.artifact.name,
            )
        finally:
            target.unlink(missing_ok=True)

    def _send_image(self, open_id: str, path: Path, _name: str = "") -> bool:
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_image_request_body import (
            CreateImageRequestBody,
        )
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        with path.open("rb") as stream:
            upload = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(stream)
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.image.create(upload)
        if not response.success() or not response.data:
            return False
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("image")
                .content(json.dumps({"image_key": response.data.image_key}))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            sent = self._get_lark_client().im.v1.message.create(request)
        return sent.code == 0

    def _send_file(self, open_id: str, path: Path, name: str = "") -> bool:
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateMessageRequest
        from lark_oapi.api.im.v1.model.create_file_request_body import (
            CreateFileRequestBody,
        )
        from lark_oapi.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        with path.open("rb") as stream:
            upload = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(name or path.name)
                    .file(stream)
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.file.create(upload)
        if not response.success() or not response.data:
            return False
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("file")
                .content(json.dumps({"file_key": response.data.file_key}))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            sent = self._get_lark_client().im.v1.message.create(request)
        return sent.code == 0

    async def _client_for(self, open_id: str) -> CoreClient:
        current = self._clients.get(open_id)
        if current is not None and current.is_connected:
            return current
        if current is not None:
            await current.disconnect()
        signing_key = resolve_local_service_token(self._paths)
        principal = f"personal:feishu:{_principal_for_log(open_id)}"
        credential = issue_principal_credential(signing_key, principal)

        async def confirm(message: ConfirmationRequestedMessage) -> bool:
            return await self._confirm_tool(open_id, message)

        client = await CoreClient.connect(
            f"ws://{self._config.service_host}:{self._config.service_port}",
            credential,
            confirmation_handler=confirm,
        )
        self._clients[open_id] = client
        return client
