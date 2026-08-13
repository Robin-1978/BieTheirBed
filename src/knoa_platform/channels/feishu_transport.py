"""Feishu adapter that speaks only the public Core WebSocket API."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from knoa_platform.branding import ASSISTANT_NAME
from knoa_platform.channels.feishu_cards import (
    _principal_for_log,
    _render_card_markdown,
    _split_plain_text,
    _split_text,
)
from knoa_platform.service.core_client import CoreClient
from knoa_platform.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from knoa_platform.tasks import (
    TaskEvent,
    TaskState,
)

logger = logging.getLogger(__name__)
_CONFIRM_TIMEOUT_SECONDS = 110.0
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^\n)]*\)")
_STREAM_PATCH_INTERVAL_SECONDS = 0.6
_CARD_MARKDOWN_CHARS = 3500
_CARD_TABLES_PER_CHUNK = 3
_PROGRESS_REASONING_CHARS = 700
_PROGRESS_STEPS_CHARS = 1000
_PROGRESS_DRAFT_CHARS = 900
_PROGRESS_TIMELINE_CHARS = (
    _PROGRESS_REASONING_CHARS
    + _PROGRESS_STEPS_CHARS
    + _PROGRESS_DRAFT_CHARS
)
_TEXT_MESSAGE_CHARS = 4000
_MAX_CORE_ARTIFACT_RAW_BYTES = 45 * 1024 * 1024
_LONG_RESULT_CARD_CHARS = 12_000
_LONG_RESULT_PREVIEW_CHARS = 1_800
_PRINCIPAL_WATCH_RETRY_SECONDS = 2.0
_TASK_TERMINAL_EVENT_TYPES = frozenset({"completed", "failed", "cancelled"})
_TASK_STATE_LABELS = {
    TaskState.QUEUED: "排队中",
    TaskState.RUNNING: "进行中",
    TaskState.WAITING_APPROVAL: "等待确认",
    TaskState.PAUSED: "已暂停",
    TaskState.COMPLETED: "已完成",
    TaskState.FAILED: "出错",
    TaskState.CANCELLED: "已停止",
}

class FeishuTransportMixin:

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

    def _add_reaction(self, message_id: str, emoji_type: str = "Typing") -> str:
        if not message_id:
            return ""
        try:
            from lark_oapi.api.im.v1 import CreateMessageReactionRequest
            from lark_oapi.api.im.v1.model.create_message_reaction_request_body import (
                CreateMessageReactionRequestBody,
            )
            from lark_oapi.api.im.v1.model.emoji import Emoji

            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(
                        Emoji.builder().emoji_type(emoji_type).build()
                    )
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.message_reaction.create(
                    request
                )
            if response.success() and response.data:
                return response.data.reaction_id or ""
            logger.debug(
                "Feishu reaction create failed code=%s msg=%s",
                response.code,
                response.msg,
            )
        except Exception:
            logger.debug("Feishu reaction create failed", exc_info=True)
        return ""

    def _remove_reaction(self, message_id: str, reaction_id: str) -> None:
        if not message_id or not reaction_id:
            return
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.message_reaction.delete(
                    request
                )
            if not response.success():
                logger.debug(
                    "Feishu reaction delete failed code=%s msg=%s",
                    response.code,
                    response.msg,
                )
        except Exception:
            logger.debug("Feishu reaction delete failed", exc_info=True)

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

    def _send_long_text(self, open_id: str, text: str) -> bool:
        succeeded = True
        for chunk in _split_plain_text(text, _TEXT_MESSAGE_CHARS):
            if not self._send_text(open_id, chunk):
                succeeded = False
        return succeeded

    @staticmethod
    def _text_card(
        text: str,
        template: str,
        title: str,
    ) -> dict[str, Any]:
        return {
            "schema": "2.0",
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {
                "elements": [{"tag": "markdown", "content": text}],
            },
        }

    def _send_card_returning_id(
        self,
        open_id: str,
        card: dict[str, Any],
    ) -> str | None:
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
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message.create(request)
        if response.code == 0 and response.data:
            message_id = getattr(response.data, "message_id", "") or ""
            logger.info(
                "Feishu card sent principal=%s msg_id=%s",
                _principal_for_log(open_id),
                message_id,
            )
            return message_id or None
        logger.error(
            "Feishu card send failed code=%s msg=%s",
            response.code,
            response.msg,
        )
        return None

    def _update_card(self, message_id: str, card: dict[str, Any]) -> bool:
        try:
            from lark_oapi.api.im.v1 import PatchMessageRequest
            from lark_oapi.api.im.v1.model.patch_message_request_body import (
                PatchMessageRequestBody,
            )

            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = self._get_lark_client().im.v1.message.patch(request)
            if response.code == 0:
                return True
            logger.warning(
                "Feishu card update failed code=%s msg=%s",
                response.code,
                response.msg,
            )
        except Exception:
            logger.warning("Feishu card update failed", exc_info=True)
        return False

    def _send_card(
        self,
        open_id: str,
        text: str,
        template: str = "blue",
        title: str = ASSISTANT_NAME,
    ) -> bool:
        rendered = _render_card_markdown(text)
        chunks = _split_text(rendered)
        succeeded = True
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            chunk_title = title if total == 1 else f"{title}（{index}/{total}）"
            card = self._text_card(chunk, template, chunk_title)
            chunk_succeeded = False
            try:
                message_id = self._send_card_returning_id(open_id, card)
            except Exception:
                logger.exception("Feishu card send failed")
                message_id = None
            if message_id is not None:
                chunk_succeeded = True
            else:
                retry_chunks = _split_text(
                    chunk,
                    max(1000, _CARD_MARKDOWN_CHARS // 2),
                    max_tables=1,
                )
                if len(retry_chunks) > 1:
                    chunk_succeeded = True
                    retry_total = len(retry_chunks)
                    for retry_index, retry_chunk in enumerate(
                        retry_chunks,
                        start=1,
                    ):
                        retry_title = (
                            f"{chunk_title}（{retry_index}/{retry_total}）"
                        )
                        retry_card = self._text_card(
                            retry_chunk,
                            template,
                            retry_title,
                        )
                        try:
                            retry_message_id = self._send_card_returning_id(
                                open_id,
                                retry_card,
                            )
                        except Exception:
                            logger.exception("Feishu card retry failed")
                            retry_message_id = None
                        if retry_message_id is None:
                            chunk_succeeded = self._send_long_text(
                                open_id,
                                f"{retry_title}\n\n{retry_chunk}",
                            ) and chunk_succeeded
                else:
                    chunk_succeeded = self._send_long_text(
                        open_id,
                        f"{chunk_title}\n\n{chunk}",
                    )
            succeeded = succeeded and chunk_succeeded
        return succeeded

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

    def _download_file(
        self,
        message_id: str,
        file_key: str,
        file_name: str,
    ) -> tuple[bytes, str]:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type("file")
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message_resource.get(request)
        if not response.success() or not response.file:
            raise RuntimeError(
                f"Feishu file download failed: {response.code} {response.msg}"
            )
        media_type = (
            mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        )
        return response.file.read(), media_type

    def _download_audio(
        self,
        message_id: str,
        file_key: str,
    ) -> tuple[bytes, str, str]:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type("file")
            .build()
        )
        with self._lark_lock:
            response = self._get_lark_client().im.v1.message_resource.get(request)
        if not response.success() or not response.file:
            raise RuntimeError(
                f"Feishu audio download failed: {response.code} {response.msg}"
            )
        data = response.file.read()
        if data.startswith(b"OggS"):
            return data, "audio/ogg", "voice-message.ogg"
        if data.startswith(b"fLaC"):
            return data, "audio/flac", "voice-message.flac"
        if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
            return data, "audio/wav", "voice-message.wav"
        if data.startswith(b"#!AMR"):
            return data, "audio/amr", "voice-message.amr"
        if data.startswith(b"ID3") or data[:2] in {
            b"\xff\xfb",
            b"\xff\xf3",
            b"\xff\xf2",
        }:
            return data, "audio/mpeg", "voice-message.mp3"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            return data, "audio/mp4", "voice-message.m4a"
        return data, "audio/ogg", "voice-message.ogg"

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
        lock = self._client_locks.setdefault(open_id, asyncio.Lock())
        async with lock:
            current = self._clients.get(open_id)
            if current is not None and current.is_connected:
                return current
            if current is not None:
                await current.disconnect()
            signing_key = resolve_local_service_token(self._paths)
            principal = self._config.owner_principal_id
            credential = issue_principal_credential(signing_key, principal)

            async def confirm(message: TaskEvent) -> bool:
                return await self._confirm_tool(open_id, message)

            client = await CoreClient.connect(
                f"ws://{self._config.service_host}:{self._config.service_port}",
                credential,
                approval_handler=confirm,
                max_buffered_task_events=4096,
            )
            self._clients[open_id] = client
            return client
