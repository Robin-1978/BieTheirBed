from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import warnings
from pathlib import Path
from typing import Any

from pc_assistant.channels.base import ChannelBase
from pc_assistant.harness.confirm import CONFIRM_TIMEOUT
from pc_assistant.redaction import redact_tool_parameters
from pc_assistant.security.totp import TotpUnlockBroker

#: Bound for a full turn (confirm prompt + agent response). Must exceed
#: CONFIRM_TIMEOUT so a pending confirmation can still complete.
_TURN_PROCESS_TIMEOUT = 180.0

if not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass

os.environ.setdefault(
    "NO_PROXY",
    "msg-frontier.feishu.cn,open.feishu.cn,feishu.cn,larkoffice.com,127.0.0.1,localhost",
)
os.environ.setdefault(
    "no_proxy",
    "msg-frontier.feishu.cn,open.feishu.cn,feishu.cn,larkoffice.com,127.0.0.1,localhost",
)
_feishu_hosts = [
    "msg-frontier.feishu.cn",
    "open.feishu.cn",
    "feishu.cn",
    "larkoffice.com",
]
for _h in _feishu_hosts:
    existing = os.environ.get("NO_PROXY", "")
    if _h not in existing:
        os.environ["NO_PROXY"] = f"{existing},{_h}" if existing else _h
    existing_lower = os.environ.get("no_proxy", "")
    if _h not in existing_lower:
        os.environ["no_proxy"] = f"{existing_lower},{_h}" if existing_lower else _h

warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("websockets").setLevel(logging.CRITICAL)
logging.getLogger("lark_oapi").setLevel(logging.WARNING)
logging.getLogger("Lark").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _principal_for_log(open_id: str) -> str:
    """Return a stable non-reversible identifier for operational logs."""
    if not open_id:
        return "unknown"
    return hashlib.sha256(open_id.encode("utf-8")).hexdigest()[:10]


def render_feishu_markdown(text: str) -> str:
    """Adapt only the Markdown constructs needing a stable card fallback.

    Feishu's ``lark_md`` supports pipe tables, so table rows are deliberately
    passed through unchanged.  The model's canonical transcript remains the
    original Markdown; this function is only a delivery-boundary adapter.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rendered: list[str] = []
    in_code = False
    code_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                rendered.extend(f"`{code}`" for code in code_lines)
                code_lines = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue

        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            rendered.append(f"**{heading.group(1).strip()}**")
            i += 1
            continue

        rendered.append(line)
        i += 1

    if in_code:
        rendered.extend(f"`{code}`" for code in code_lines)
    return "\n".join(rendered)


def _markdown_to_card_elements(text: str) -> list[dict[str, Any]]:
    """Build one Card JSON 2.0 Markdown component.

    The JSON 2.0 rich-text Markdown component natively renders pipe tables,
    emphasis, links, code, and other supported Markdown syntax. Keeping the
    complete document in one component avoids flattening table cells to plain
    text and preserves the model's formatting at the delivery boundary.
    """
    content = text.strip()
    return [{"tag": "markdown", "content": content}] if content else []


def _patch_ws_card_dispatch(ws_client: Any) -> None:
    """Monkey-patch lark_oapi WS client to dispatch CARD frames.

    The upstream SDK (<=1.6.5) silently drops MessageType.CARD frames in
    ``_handle_data_frame``, breaking all interactive card button callbacks
    over WebSocket. This patch routes CARD frames through the same
    ``_event_handler._do_without_validation`` path used for EVENT frames.
    See: https://github.com/larksuite/oapi-sdk-python/issues/126
    """
    try:
        import types
        from lark_oapi.ws.client import MessageType
        import base64
        import http

        original = ws_client._handle_data_frame

        from lark_oapi.ws.const import (
            HEADER_TYPE, HEADER_MESSAGE_ID,
            HEADER_SUM, HEADER_SEQ, HEADER_BIZ_RT,
        )
        from lark_oapi.ws.model import Response
        from lark_oapi import JSON as LarkJSON

        async def _patched_handle_data_frame(self, frame):
            hs = frame.headers
            type_ = ""
            for h in hs:
                if h.key == HEADER_TYPE:
                    type_ = h.value
                    break

            if type_ and MessageType(type_) == MessageType.CARD:
                pl = frame.payload
                sum_val = "1"
                seq_val = "1"
                msg_id = ""
                for h in hs:
                    if h.key == HEADER_SUM:
                        sum_val = h.value
                    elif h.key == HEADER_SEQ:
                        seq_val = h.value
                    elif h.key == HEADER_MESSAGE_ID:
                        msg_id = h.value

                if int(sum_val) > 1:
                    pl = self._combine(msg_id, int(sum_val), int(seq_val), pl)
                    if pl is None:
                        return

                resp = Response(code=http.HTTPStatus.OK)
                try:
                    import time as _time
                    start = int(round(_time.time() * 1000))
                    result = self._event_handler._do_without_validation(pl)
                    end = int(round(_time.time() * 1000))
                    header = hs.add()
                    header.key = HEADER_BIZ_RT
                    header.value = str(end - start)
                    if result is not None:
                        resp.data = base64.b64encode(
                            LarkJSON.marshal(result).encode("utf-8")
                        )
                except Exception as e:
                    logger.error("[WS-CARD] dispatch error: %s", e, exc_info=True)
                    resp = Response(code=http.HTTPStatus.INTERNAL_SERVER_ERROR)

                frame.payload = LarkJSON.marshal(resp).encode("utf-8")
                await self._write_message(frame.SerializeToString())
            else:
                await original(frame)

        ws_client._handle_data_frame = types.MethodType(
            _patched_handle_data_frame, ws_client
        )
        logger.info("[WS] CARD frame dispatch patch applied")
    except Exception as e:
        logger.warning("[WS] Could not patch CARD dispatch: %s", e)


class FeishuChannel(ChannelBase):
    name = "feishu"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        receive_id: str = "",
        receive_id_type: str = "open_id",
        runtime_root: str = "",
        remote_unlock_enabled: bool = False,
        remote_unlock_allowed_open_ids: list[str] | None = None,
        remote_unlock_totp_secret_file: str = "secrets/unlock.totp",
        remote_unlock_totp_period_seconds: int = 30,
        remote_unlock_max_attempts: int = 3,
        remote_unlock_lockout_seconds: int = 300,
    ) -> None:
        from pc_assistant.runtime import RuntimePaths

        self._app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self._app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._receive_id = receive_id or os.environ.get("FEISHU_RECEIVE_ID", "")
        self._receive_id_type = receive_id_type or os.environ.get(
            "FEISHU_RECEIVE_ID_TYPE", "open_id"
        )
        self._inbox_dir = RuntimePaths.from_root(runtime_root or None).attachments / "feishu-inbox"
        runtime_paths = RuntimePaths.from_root(runtime_root or None)
        self._received_open_id_file = runtime_paths.data / "feishu_open_id"
        secret_path = runtime_paths.resolve(remote_unlock_totp_secret_file, default_parent=runtime_paths.root / "secrets")
        self._unlock_broker = TotpUnlockBroker(secret_path, remote_unlock_allowed_open_ids or [], enabled=remote_unlock_enabled, period=remote_unlock_totp_period_seconds, max_attempts=remote_unlock_max_attempts, lockout_seconds=remote_unlock_lockout_seconds)

        self._lark_client = None
        self._lark_lock = threading.RLock()

        self._msg_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._msg_seen: dict[str, float] = {}
        self._msg_seen_lock = threading.Lock()
        self._recent_texts: dict[str, float] = {}
        self._recent_texts_lock = threading.Lock()
        self._last_ws_activity = time.time()
        self._ws_loop_ref: asyncio.AbstractEventLoop | None = None
        self._chat_id_cache: dict[str, str] = {}
        self._last_poll_ts = 0.0
        self._poll_interval = 30
        self._ws_recv_count = 0

        self._agent: Any = None
        self._agent_loop: asyncio.AbstractEventLoop | None = None

        self._pending_confirm: dict[str, dict[str, Any]] = {}
        self._pending_confirm_lock = threading.Lock()

        self._user_locks: dict[str, asyncio.Lock] = {}

        # Feishu image messages don't carry a natural-language question.
        # Store them first, then bind the next message or an explicit reply to
        # the exact attachment rather than inventing a fixed prompt.
        self._image_refs_lock = threading.Lock()
        self._image_refs_by_message: dict[str, tuple[str, float]] = {}
        self._pending_image_refs_by_user: dict[str, list[tuple[str, float]]] = {}
        self._active_image_refs_by_user: dict[str, list[tuple[str, float]]] = {}

        self._running = False

    async def start(self, agent: Any) -> None:
        if not self._app_id or not self._app_secret:
            logger.warning("Feishu APP_ID or APP_SECRET not configured, skipping start")
            return

        self._agent = agent
        self._agent_loop = asyncio.get_running_loop()
        self._running = True

        self._ensure_worker()
        self._last_ws_activity = time.time()

        t = threading.Thread(target=self._ws_loop, daemon=True)
        t.start()
        logger.info("Feishu WS connection thread started")

        t2 = threading.Thread(target=self._watchdog, daemon=True)
        t2.start()
        logger.info("Feishu watchdog + poll thread started")

        logger.info("FeishuChannel started")

    async def stop(self) -> None:
        self._running = False
        self._msg_queue.put(None)
        logger.info("FeishuChannel stopping...")

    def send_message(self, recipient_id: str, text: str) -> bool:
        rid = recipient_id or self._get_receive_id()
        if not rid:
            return False
        return self._send_text(rid, text)

    def send_card(self, recipient_id: str, card: dict) -> bool:
        rid = recipient_id or self._get_receive_id()
        if not rid:
            return False
        return self._send_card(rid, card)

    def deliver_scheduled_result(
        self,
        session_id: str,
        task_name: str,
        result: dict[str, Any],
        artifact_resolver: Any = None,
    ) -> bool:
        """Deliver a background Agent run to its Feishu conversation."""
        if not session_id.startswith("feishu:"):
            return False
        open_id = session_id.removeprefix("feishu:")
        answer = result.get("result") or result.get("error") or result.get("message") or "任务已完成"
        if not isinstance(answer, str):
            answer = json.dumps(answer, ensure_ascii=False, default=str)
        delivered_artifacts = 0
        if artifact_resolver is not None:
            for artifact_ref in result.get("artifacts", [])[:5]:
                artifact_id = str(artifact_ref.get("artifact_id", ""))
                if not artifact_id:
                    continue
                try:
                    artifact = artifact_resolver(session_id, artifact_id)
                    if self._deliver_artifact(open_id, artifact):
                        delivered_artifacts += 1
                except Exception:
                    logger.exception("[SCHEDULE] Failed to deliver artifact %s", artifact_id)
        if delivered_artifacts:
            answer += f"\n\n📎 已发送 {delivered_artifacts} 个附件"
        card = self._build_response_card(
            f"**定时任务：{task_name}**\n\n{answer}",
            [],
            bool(result.get("error") or result.get("executed") is False),
        )
        return self._send_card(open_id, card) or self._send_text(open_id, answer)

    # ================================================================
    # Lark Client
    # ================================================================

    def _get_lark_client(self):
        if self._lark_client is not None:
            return self._lark_client
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

    def _get_receive_id(self) -> str:
        if self._receive_id:
            return self._receive_id
        if self._received_open_id_file.exists():
            try:
                with open(self._received_open_id_file, "r") as f:
                    return f.read().strip()
            except Exception:
                pass
        return ""

    def _get_last_open_id(self) -> str:
        """Get the most recently active Feishu user open_id."""
        return self._get_receive_id()

    def _download_image(self, image_key: str, message_id: str) -> str:
        """Download an image attached to a specific Feishu message."""
        if not message_id:
            raise ValueError("message_id is required to download a Feishu message image")
        client = self._get_lark_client()
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        with self._lark_lock:
            resp = client.im.v1.message_resource.get(request)
        if not resp.success() or not resp.file:
            raise RuntimeError(f"Feishu image download failed: {resp.code} {resp.msg}")
        import uuid

        data = resp.file.read()
        suffix = ".img"
        try:
            import io
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                suffix = {
                    "PNG": ".png",
                    "JPEG": ".jpg",
                    "WEBP": ".webp",
                    "GIF": ".gif",
                }.get(str(image.format or "").upper(), ".img")
        except Exception:
            pass
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        path = self._inbox_dir / f"feishu-{uuid.uuid4().hex}{suffix}"
        with path.open("wb") as fh:
            fh.write(data)
        return str(path)

    def _save_open_id(self, open_id: str) -> None:
        try:
            self._received_open_id_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._received_open_id_file, "w") as f:
                f.write(open_id)
            os.chmod(self._received_open_id_file, 0o600)
            logger.info("Saved Feishu binding")
        except Exception as e:
            logger.warning("Failed to save open_id: %s", e)

    def _claim_message(self, message_id: str) -> bool:
        """Atomically claim an inbound message across WS and poll recovery."""
        if not message_id:
            return True
        now = time.time()
        with self._msg_seen_lock:
            if message_id in self._msg_seen:
                return False
            self._msg_seen[message_id] = now
            if len(self._msg_seen) > 1000:
                self._msg_seen = {
                    key: seen_at for key, seen_at in self._msg_seen.items()
                    if now - seen_at <= 300
                }
        return True

    def _remember_image_ref(self, open_id: str, message_id: str, artifact_id: str) -> None:
        expires_at = time.time() + 3600
        with self._image_refs_lock:
            if message_id:
                self._image_refs_by_message[message_id] = (artifact_id, expires_at)
            pending = self._pending_image_refs_by_user.setdefault(open_id, [])
            pending.append((artifact_id, expires_at))
            self._pending_image_refs_by_user[open_id] = pending[-4:]
            self._active_image_refs_by_user[open_id] = pending[-4:]
            self._prune_image_refs_locked()

    def _prune_image_refs_locked(self) -> None:
        now = time.time()
        self._image_refs_by_message = {
            key: value for key, value in self._image_refs_by_message.items()
            if value[1] > now
        }
        for open_id, refs in list(self._pending_image_refs_by_user.items()):
            live = [ref for ref in refs if ref[1] > now]
            if live:
                self._pending_image_refs_by_user[open_id] = live
            else:
                self._pending_image_refs_by_user.pop(open_id, None)
        for open_id, refs in list(self._active_image_refs_by_user.items()):
            live = [ref for ref in refs if ref[1] > now]
            if live:
                self._active_image_refs_by_user[open_id] = live
            else:
                self._active_image_refs_by_user.pop(open_id, None)

    def _attachments_for_text(
        self,
        open_id: str,
        *,
        parent_id: str = "",
        root_id: str = "",
    ) -> list | None:
        from pc_assistant.model_adapter.types import ImageAttachment

        with self._image_refs_lock:
            self._prune_image_refs_locked()
            reply_ids = [item for item in (parent_id, root_id) if item]
            artifact_ids: list[str] = []
            for message_id in reply_ids:
                stored = self._image_refs_by_message.get(message_id)
                if stored and stored[0] not in artifact_ids:
                    artifact_ids.append(stored[0])

            if not reply_ids and not artifact_ids:
                pending = self._pending_image_refs_by_user.pop(open_id, [])
                artifact_ids = [artifact_id for artifact_id, _ in pending]
            elif reply_ids and not artifact_ids:
                # Replying to the bot's answer continues the active image
                # thread even though the parent message itself isn't an image.
                active = self._active_image_refs_by_user.get(open_id, [])
                artifact_ids = [artifact_id for artifact_id, _ in active]
            elif artifact_ids:
                pending = self._pending_image_refs_by_user.get(open_id, [])
                self._pending_image_refs_by_user[open_id] = [
                    item for item in pending if item[0] not in artifact_ids
                ]
            if artifact_ids:
                expires_at = time.time() + 3600
                self._active_image_refs_by_user[open_id] = [
                    (artifact_id, expires_at) for artifact_id in artifact_ids
                ]

        return [ImageAttachment.from_ref(item, caption="feishu image") for item in artifact_ids] or None

    def _accept_image_message(
        self,
        open_id: str,
        message_id: str,
        image_key: str,
    ) -> bool:
        if not self._claim_message(message_id):
            logger.info("[IMAGE] Duplicate message_id=%s, skip", message_id)
            return False
        reaction_id = self._add_reaction(message_id, "Typing")
        try:
            from pc_assistant.model_adapter.types import ImageAttachment

            path = self._download_image(image_key, message_id)
            session_id = f"feishu:{open_id}"
            stored = self._agent.store_artifact(
                session_id,
                ImageAttachment.from_path(path, caption="feishu image"),
            )
            self._remember_image_ref(open_id, message_id, stored["artifact_id"])
            self._send_text(
                open_id,
                "🖼️ 图片已收到。请直接发送问题，或回复这张图片进行追问。",
            )
            logger.info("[IMAGE] Stored feishu image message=%s path=%s", message_id, path)
            return True
        except Exception as exc:
            logger.error("[IMAGE] Failed to accept image: %s", exc, exc_info=True)
            self._send_text(open_id, "❌ 图片下载失败，请稍后重试或重新发送图片。")
            return False
        finally:
            self._remove_reaction(message_id, reaction_id)

    # ================================================================
    # Message Sending
    # ================================================================

    def _add_reaction(self, msg_id: str, emoji_type: str = "Typing") -> str:
        if not msg_id:
            return ""
        try:
            from lark_oapi.api.im.v1 import CreateMessageReactionRequest
            from lark_oapi.api.im.v1.model.create_message_reaction_request_body import (
                CreateMessageReactionRequestBody,
            )
            from lark_oapi.api.im.v1.model.emoji import Emoji

            request = (
                CreateMessageReactionRequest.builder()
                .message_id(msg_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            client = self._get_lark_client()
            if not self._lark_lock.acquire(timeout=3):
                return ""
            try:
                response = client.im.v1.message_reaction.create(request)
            finally:
                self._lark_lock.release()
            if response.success() and response.data:
                return response.data.reaction_id or ""
            logger.debug(
                "Reaction create failed (non-critical): code=%s msg=%s",
                response.code,
                response.msg,
            )
        except Exception as e:
            logger.debug("Reaction failed (non-critical): %s", e)
        return ""

    def _remove_reaction(self, msg_id: str, reaction_id: str) -> None:
        if not msg_id or not reaction_id:
            return
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(msg_id)
                .reaction_id(reaction_id)
                .build()
            )
            client = self._get_lark_client()
            if not self._lark_lock.acquire(timeout=3):
                return
            try:
                response = client.im.v1.message_reaction.delete(request)
            finally:
                self._lark_lock.release()
            if not response.success():
                logger.debug(
                    "Reaction delete failed (non-critical): code=%s msg=%s",
                    response.code,
                    response.msg,
                )
        except Exception as e:
            logger.debug("Reaction removal failed (non-critical): %s", e)

    def _send_text(self, open_id: str, text: str) -> bool:
        try:
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
                    .content(json.dumps({"text": text}))
                    .build()
                )
                .build()
            )
            client = self._get_lark_client()
            with self._lark_lock:
                resp = client.im.v1.message.create(request)
            if resp.code == 0:
                logger.info(
                    "[SEND-TEXT] OK principal=%s chars=%d",
                    _principal_for_log(open_id),
                    len(text),
                )
                return True
            else:
                logger.error("[SEND-TEXT] FAILED code=%s msg=%s", resp.code, resp.msg)
                return False
        except Exception as e:
            logger.error("[SEND-TEXT] Exception: %s", e, exc_info=True)
            return False

    def _send_card(self, open_id: str, card: dict) -> bool:
        try:
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
                    .content(json.dumps(card))
                    .build()
                )
                .build()
            )
            client = self._get_lark_client()
            with self._lark_lock:
                resp = client.im.v1.message.create(request)
            if resp.code == 0:
                logger.info("[SEND-CARD] OK principal=%s", _principal_for_log(open_id))
                return True
            else:
                logger.error("[SEND-CARD] FAILED code=%s msg=%s", resp.code, resp.msg)
                return False
        except Exception as e:
            logger.error("[SEND-CARD] Exception: %s", e, exc_info=True)
            return False

    def _send_image(self, open_id: str, path: str) -> bool:
        """Upload a local image and send it as a Feishu image message."""
        image_path = Path(path).expanduser()
        if not image_path.is_file():
            logger.error("[SEND-IMAGE] File does not exist: %s", image_path)
            return False
        try:
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateMessageRequest
            from lark_oapi.api.im.v1.model.create_image_request_body import (
                CreateImageRequestBody,
            )
            from lark_oapi.api.im.v1.model.create_message_request_body import (
                CreateMessageRequestBody,
            )

            with image_path.open("rb") as image_file:
                upload_request = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(image_file)
                        .build()
                    )
                    .build()
                )
                client = self._get_lark_client()
                with self._lark_lock:
                    upload_response = client.im.v1.image.create(upload_request)

            if not upload_response.success() or not upload_response.data:
                logger.error(
                    "[SEND-IMAGE] Upload failed code=%s msg=%s",
                    upload_response.code,
                    upload_response.msg,
                )
                return False

            image_key = upload_response.data.image_key
            message_request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("image")
                    .content(json.dumps({"image_key": image_key}))
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = client.im.v1.message.create(message_request)
            if response.code == 0:
                logger.info(
                    "[SEND-IMAGE] OK principal=%s file=%s",
                    _principal_for_log(open_id),
                    image_path.name,
                )
                return True
            logger.error("[SEND-IMAGE] Send failed code=%s msg=%s", response.code, response.msg)
            return False
        except Exception as e:
            logger.error("[SEND-IMAGE] Exception: %s", e, exc_info=True)
            return False

    def _send_file(self, open_id: str, path: str, name: str = "") -> bool:
        """Upload a generic local file and send it as a Feishu file message."""
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            logger.error("[SEND-FILE] File does not exist: %s", file_path)
            return False
        try:
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateMessageRequest
            from lark_oapi.api.im.v1.model.create_file_request_body import (
                CreateFileRequestBody,
            )
            from lark_oapi.api.im.v1.model.create_message_request_body import (
                CreateMessageRequestBody,
            )

            with file_path.open("rb") as file_handle:
                upload_request = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type("stream")
                        .file_name(name or file_path.name)
                        .file(file_handle)
                        .build()
                    )
                    .build()
                )
                client = self._get_lark_client()
                with self._lark_lock:
                    upload_response = client.im.v1.file.create(upload_request)

            if not upload_response.success() or not upload_response.data:
                logger.error(
                    "[SEND-FILE] Upload failed code=%s msg=%s",
                    upload_response.code,
                    upload_response.msg,
                )
                return False

            message_request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(open_id)
                    .msg_type("file")
                    .content(json.dumps({"file_key": upload_response.data.file_key}))
                    .build()
                )
                .build()
            )
            with self._lark_lock:
                response = client.im.v1.message.create(message_request)
            if response.code == 0:
                logger.info(
                    "[SEND-FILE] OK principal=%s file=%s",
                    _principal_for_log(open_id),
                    Path(name).name if name else file_path.name,
                )
                return True
            logger.error("[SEND-FILE] Send failed code=%s msg=%s", response.code, response.msg)
            return False
        except Exception as exc:
            logger.error("[SEND-FILE] Exception: %s", exc, exc_info=True)
            return False

    def _deliver_artifact(self, open_id: str, artifact: dict[str, Any]) -> bool:
        media_type = str(artifact.get("media_type", ""))
        path = str(artifact.get("path", ""))
        if media_type.startswith("image/"):
            return self._send_image(open_id, path)
        return self._send_file(open_id, path, str(artifact.get("name", "")))

    # ================================================================
    # Message Handling
    # ================================================================

    def _handle_message(
        self,
        open_id: str,
        text: str,
        attachments: list | None = None,
        msg_id: str = "",
        reaction_id: str = "",
    ) -> None:
        try:
            if self._agent_loop is None:
                self._send_text(open_id, "❌ Agent 未就绪")
                return

            text_stripped = text.strip()
            if not text_stripped:
                return

            if text_stripped.startswith("/"):
                if self._handle_slash_command(open_id, text_stripped):
                    return

            with self._pending_confirm_lock:
                pending = self._pending_confirm.get(open_id)
                if pending is not None:
                    self._handle_confirm(open_id, text_stripped, pending)
                    return

            future = asyncio.run_coroutine_threadsafe(
                self._process_with_agent(open_id, text_stripped, attachments=attachments),
                self._agent_loop,
            )
            try:
                future.result(timeout=_TURN_PROCESS_TIMEOUT)
            except (asyncio.TimeoutError, concurrent.futures.TimeoutError):
                future.cancel()
                self._send_text(open_id, "❌ 处理超时，已取消，请简化问题重试")
            except Exception as e:
                logger.error("[HANDLE] Agent processing failed: %s", e, exc_info=True)
                self._send_text(open_id, f"❌ 处理失败: {e}")
        finally:
            self._remove_reaction(msg_id, reaction_id)

    def _handle_slash_command(self, open_id: str, text: str) -> bool:
        """Handle slash commands in Feishu. Returns True if handled."""
        cmd = text.split()[0].lower()

        if cmd == "/unlock":
            parts = text.split()
            code = parts[1] if len(parts) == 2 else ""
            ok, message = self._unlock_broker.verify_and_unlock(open_id, code)
            self._send_text(open_id, ("✅ " if ok else "❌ ") + message)
            return True

        if cmd in ("/help", "/帮助"):
            self._send_card(open_id, {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "indigo",
                    "title": {"tag": "plain_text", "content": "📖 命令帮助"},
                },
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": (
                        "`/help` - 显示此帮助\n"
                        "`/clear` - 清空对话历史\n"
                        "`/unlock <验证码>` - 使用本地TOTP解锁桌面\n"
                        "`/status` - 查看 Agent 状态\n"
                        "`/tools` - 列出可用工具\n"
                        "`/config` - 查看当前配置"
                    )},
                }],
            })
            return True

        if cmd == "/clear":
            if self._agent is not None:
                session_id = f"feishu:{open_id}"
                try:
                    self._agent.drop_session(session_id)
                except Exception:
                    pass
            self._send_text(open_id, "✅ 对话历史已清空")
            return True

        if cmd == "/status":
            if self._agent is not None:
                status = asyncio.run_coroutine_threadsafe(
                    self._agent.get_status(), self._agent_loop
                ).result(timeout=_TURN_PROCESS_TIMEOUT)
                session_id = f"feishu:{open_id}"
                session_stats = [s for s in self._agent.session_stats() if s.get("session_id") == session_id]
                session_info = session_stats[0] if session_stats else {}

                prompt_tokens = session_info.get("total_prompt_tokens", 0)
                comp_tokens = session_info.get("total_completion_tokens", 0)
                iterations = session_info.get("total_iterations", 0)

                info = (
                    f"**Provider**: {status.get('provider', '?')}\n"
                    f"**Model**: {status.get('model', '?')}\n"
                    f"**Status**: {status.get('status', '?')}\n"
                    f"**LLM Connected**: {'✅' if status.get('connected') else '❌'}\n"
                    f"**Active Sessions**: {status.get('active_sessions', 0)}\n"
                    f"**Available Tools**: {len(status.get('tools', []))}\n"
                    "---\n"
                    f"📊 **Your Session** (`{session_id}`)\n"
                    f"**Prompt Tokens**: {prompt_tokens:,}\n"
                    f"**Completion Tokens**: {comp_tokens:,}\n"
                    f"**Total Tokens**: {prompt_tokens + comp_tokens:,}\n"
                    f"**Iterations**: {iterations}\n"
                    f"**Messages**: {session_info.get('messages', 0)}"
                )
                self._send_card(open_id, {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "green",
                        "title": {"tag": "plain_text", "content": "📊 Agent Status"},
                    },
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": info}}],
                })
            else:
                self._send_text(open_id, "❌ Agent 未初始化")
            return True

        if cmd == "/tools":
            if self._agent is not None:
                tools = self._agent.registry.list_tools()
                tools_str = "  ".join(f"`{t}`" for t in tools)
                self._send_card(open_id, {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "purple",
                        "title": {"tag": "plain_text", "content": f"🔧 Available Tools ({len(tools)})"},
                    },
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": tools_str}}],
                })
            return True

        if cmd == "/config":
            if self._agent is not None:
                cfg = self._agent.config
                model = cfg.resolve_model()
                info = (
                    f"**LLM**: {model.alias} ({model.provider_name}) @ {model.server_url}\n"
                    f"**Max iterations**: {cfg.max_iterations}\n"
                    f"**Max tokens**: {cfg.max_tokens}\n"
                    f"**Temperature**: {cfg.llm_temperature}"
                )
                self._send_text(open_id, info)
            return True

        return False

    def _handle_confirm(
        self, open_id: str, text: str, pending: dict[str, Any]
    ) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower() if parts else ""
        code = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("确认", "confirm", "ok", "yes", "y"):
            if not code:
                self._send_text(open_id, "用法: `确认 <验证码>`\n例如: `确认 1234`")
                return
            with self._pending_confirm_lock:
                self._pending_confirm.pop(open_id, None)
            if time.time() - pending["ts"] > CONFIRM_TIMEOUT:
                self._send_text(open_id, "❌ 确认码已过期，请重新操作")
                return
            if code != pending["code"]:
                with self._pending_confirm_lock:
                    self._pending_confirm[open_id] = pending
                self._send_text(open_id, "❌ 验证码错误，请重试")
                return
            try:
                result = pending["fn"]()
                if isinstance(result, str):
                    self._send_text(open_id, result)
            except Exception as e:
                self._send_text(open_id, f"❌ 操作执行失败: {e}")
        elif cmd in ("取消", "cancel", "no", "n"):
            with self._pending_confirm_lock:
                self._pending_confirm.pop(open_id, None)
            cancel_fn = pending.get("cancel_fn")
            if cancel_fn:
                try:
                    cancel_fn()
                except Exception:
                    pass
            self._send_text(open_id, "✅ 操作已取消")
        else:
            self._send_text(
                open_id,
                f"⚠️ 有操作等待确认(验证码 `{pending['code']}`)。\n"
                f"请回复 `确认 {pending['code']}` 或 `取消`，其他内容不会处理。",
            )

    def _request_confirm(
        self, open_id: str, action_desc: str, action_fn: Any
    ) -> bool:
        import random

        code = f"{random.randint(1000, 9999)}"
        with self._pending_confirm_lock:
            self._pending_confirm[open_id] = {
                "code": code,
                "fn": action_fn,
                "desc": action_desc,
                "ts": time.time(),
            }
            if len(self._pending_confirm) > 100:
                now = time.time()
                expired = [k for k, v in self._pending_confirm.items() if now - v["ts"] > CONFIRM_TIMEOUT]
                for k in expired:
                    del self._pending_confirm[k]
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "⚠️ 操作确认"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": action_desc},
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 批准执行"},
                            "type": "primary",
                            "value": {"confirm_code": code, "approved": True},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                            "type": "danger",
                            "value": {"confirm_code": code, "approved": False},
                        },
                    ],
                },
                {
                    "tag": "note",
                    "elements": [{
                        "tag": "plain_text",
                        "content": f"⏱ {int(CONFIRM_TIMEOUT)}秒内有效 | 也可发送: 确认 {code} / 取消",
                    }],
                },
            ],
        }
        if not self._send_card(open_id, card):
            return self._send_text(
                open_id,
                f"⚠️ **请确认操作**\n{action_desc}\n\n"
                f"发送 `确认 {code}` 执行，`取消` 放弃，{int(CONFIRM_TIMEOUT)}秒内有效",
            )
        return True

    def _handle_card_confirm(
        self, open_id: str, confirm_code: str, approved: bool
    ) -> None:
        """Handle button click from a confirmation card."""
        with self._pending_confirm_lock:
            pending = self._pending_confirm.get(open_id)
            if pending is None:
                return
            if pending["code"] != confirm_code:
                return
            self._pending_confirm.pop(open_id, None)

        if time.time() - pending["ts"] > CONFIRM_TIMEOUT:
            self._send_text(open_id, "❌ 确认已过期，请重新操作")
            return

        if approved:
            try:
                result = pending["fn"]()
                if isinstance(result, str):
                    self._send_text(open_id, result)
            except Exception as e:
                self._send_text(open_id, f"❌ 操作执行失败: {e}")
        else:
            pending_fn = pending.get("cancel_fn")
            if pending_fn:
                try:
                    pending_fn()
                except Exception:
                    pass
            self._send_text(open_id, "✅ 操作已取消")

    async def _process_with_agent(self, open_id: str, text: str, attachments: list | None = None) -> None:
        if self._agent is None:
            self._send_text(open_id, "❌ Agent 未初始化")
            return

        lock = self._get_user_lock(open_id)
        async with lock:
            await self._process_with_agent_locked(open_id, text, attachments=attachments)

    def _get_user_lock(self, open_id: str) -> asyncio.Lock:
        """Return the per-user lock, evicting idle entries to bound memory."""
        lock = self._user_locks.get(open_id)
        if lock is not None:
            return lock
        if len(self._user_locks) >= 100:
            for old_id, old_lock in list(self._user_locks.items()):
                if not old_lock.locked():
                    del self._user_locks[old_id]
                    break
        lock = asyncio.Lock()
        self._user_locks[open_id] = lock
        return lock

    async def _process_with_agent_locked(self, open_id: str, text: str, attachments: list | None = None) -> None:
        feishu_session_id = f"feishu:{open_id}"
        agent_loop = asyncio.get_running_loop()

        async def feishu_confirm(tool_name: str, args: dict[str, Any]) -> bool:
            args_brief = json.dumps(
                redact_tool_parameters(tool_name, args), ensure_ascii=False
            )[:120]
            action_desc = f"\U0001f527 **{tool_name}**\n`{args_brief}`"

            confirm_event = asyncio.Event()
            confirm_result = [False]

            def _set_result(approved: bool) -> str:
                confirm_result[0] = approved
                agent_loop.call_soon_threadsafe(confirm_event.set)
                return "\u2705 \u64cd\u4f5c\u5df2\u6267\u884c" if approved else ""

            def on_confirmed():
                return _set_result(True)

            def on_cancelled():
                _set_result(False)

            with self._pending_confirm_lock:
                old = self._pending_confirm.get(open_id)
                if old:
                    del self._pending_confirm[open_id]
            self._request_confirm(open_id, action_desc, on_confirmed)
            with self._pending_confirm_lock:
                if open_id in self._pending_confirm:
                    self._pending_confirm[open_id]["cancel_fn"] = on_cancelled

            try:
                await asyncio.wait_for(confirm_event.wait(), timeout=CONFIRM_TIMEOUT)
                return confirm_result[0]
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return False
            finally:
                with self._pending_confirm_lock:
                    self._pending_confirm.pop(open_id, None)

        try:
            tool_calls_info: list[str] = []
            thinking_chunks: list[str] = []
            final_answer = ""
            error_msg = ""
            artifact_refs: list[dict[str, Any]] = []
            artifact_ids: set[str] = set()

            async for event in self._agent.run(
                text,
                session_id=feishu_session_id,
                confirm_callback=feishu_confirm,
                attachments=attachments,
            ):
                if event.type == "tool_call" and not event.blocked:
                    tool_name = event.tool_name
                    tool_args = event.tool_args
                    args_brief = json.dumps(
                        redact_tool_parameters(tool_name, tool_args),
                        ensure_ascii=False,
                    )[:80]
                    tool_calls_info.append(f"🔧 {tool_name}({args_brief})")
                elif event.type == "artifact" and event.artifact is not None:
                    artifact_ref = event.artifact.model_dump()
                    artifact_id = event.artifact.artifact_id
                    if artifact_id and artifact_id not in artifact_ids:
                        artifact_ids.add(artifact_id)
                        artifact_refs.append(artifact_ref)
                elif event.type == "stream_think_delta":
                    if event.content:
                        thinking_chunks.append(event.content)
                elif event.type == "final_answer":
                    final_answer = event.content
                elif event.type == "error":
                    error_msg = event.content
                elif event.type == "cancelled":
                    error_msg = event.content
                elif event.type == "iteration_limit":
                    error_msg = event.content

            if tool_calls_info and final_answer:
                tools_summary = "\n".join(tool_calls_info[:5])
                if len(tool_calls_info) > 5:
                    tools_summary += f"\n... +{len(tool_calls_info) - 5} more"
                plain_response = f"{tools_summary}\n\n{final_answer}"
            elif final_answer:
                plain_response = final_answer
                tools_summary = ""
            elif error_msg:
                plain_response = f"❌ {error_msg}"
                tools_summary = ""
            else:
                plain_response = "⚠️ 未获得有效回复，请重试"
                tools_summary = ""

            # Internal reasoning is never forwarded to an external channel.
            thinking_text = ""
            delivery_failures: list[str] = []
            for artifact_ref in artifact_refs[:5]:
                artifact_id = str(artifact_ref.get("artifact_id", ""))
                try:
                    resolved = self._agent.resolve_artifact(feishu_session_id, artifact_id)
                except (KeyError, ValueError, AttributeError) as exc:
                    logger.error("[PROCESS] Failed to resolve artifact %s: %s", artifact_id, exc)
                    delivery_failures.append(str(artifact_ref.get("name") or artifact_id))
                    continue
                if not self._deliver_artifact(open_id, resolved):
                    logger.error("[PROCESS] Failed to deliver artifact: %s", artifact_id)
                    delivery_failures.append(str(resolved.get("name") or artifact_id))
                    continue
                try:
                    self._agent.mark_artifact_delivered(feishu_session_id, artifact_id)
                except (KeyError, AttributeError) as exc:
                    logger.warning(
                        "[PROCESS] Delivered artifact but failed to record acknowledgement %s: %s",
                        artifact_id,
                        exc,
                    )
            response_answer = final_answer or plain_response
            if delivery_failures:
                response_answer += "\n\n⚠️ 以下附件交付失败：" + "、".join(delivery_failures)
            card = self._build_response_card(
                response_answer,
                tool_calls_info,
                bool(error_msg or delivery_failures),
                thinking=thinking_text[:500] if thinking_text else "",
            )
            if not self._send_card(open_id, card):
                self._send_long_text(open_id, response_answer)

        except Exception as e:
            logger.error("[PROCESS] Agent error: %s", e, exc_info=True)
            self._send_text(open_id, f"❌ 处理出错: {e}")

    def _build_response_card(
        self,
        answer: str,
        tool_calls: list[str],
        is_error: bool,
        thinking: str = "",
    ) -> dict:
        """Build a Feishu interactive card for the agent response."""
        elements: list[dict] = []

        if thinking:
            elements.append({
                "tag": "note",
                "elements": [{
                    "tag": "markdown",
                    "content": f"💭 **思考**: {thinking}",
                }],
            })

        if tool_calls:
            tools_md = "\n".join(tool_calls[:5])
            if len(tool_calls) > 5:
                tools_md += f"\n... +{len(tool_calls) - 5} more"
            elements.append({
                "tag": "markdown",
                "content": tools_md,
            })
            elements.append({"tag": "hr"})

        content = render_feishu_markdown(answer)[:3800]
        if len(answer) > 3800:
            content += "\n\n... (内容过长已截断)"
        elements.extend(_markdown_to_card_elements(content))

        header_color = "red" if is_error else "blue"
        header_title = "❌ 处理出错" if is_error else "💬 PC Assistant"

        return {
            "schema": "2.0",
            "header": {
                "template": header_color,
                "title": {"tag": "plain_text", "content": header_title},
            },
            "body": {"elements": elements},
        }

    def _send_long_text(self, open_id: str, text: str) -> None:
        max_len = 2000
        if len(text) <= max_len:
            self._send_text(open_id, text)
        else:
            for i in range(0, len(text), max_len):
                self._send_text(open_id, text[i : i + max_len])

    # ================================================================
    # Message Queue + Worker
    # ================================================================

    def _msg_worker(self) -> None:
        self._worker_thread = threading.current_thread()
        logger.info("[WORKER] Message worker thread started")
        while self._running:
            try:
                item = self._msg_queue.get()
                if item is None:
                    logger.info("[WORKER] Received shutdown signal")
                    break
                open_id, text, msg_id, attachments = item

                if open_id and not self._receive_id:
                    self._save_open_id(open_id)

                now = time.time()
                with self._recent_texts_lock:
                    dedup_key = f"{open_id}:{text}"
                    last_time = self._recent_texts.get(dedup_key, 0)
                    if now - last_time < 30:
                        logger.info(
                            "[WORKER] Duplicate text within 30s principal=%s chars=%d, skip",
                            _principal_for_log(open_id),
                            len(text),
                        )
                        self._msg_queue.task_done()
                        continue
                    self._recent_texts[dedup_key] = now
                    if len(self._recent_texts) > 500:
                        expired_keys = [
                            k
                            for k, v in self._recent_texts.items()
                            if now - v > 120
                        ]
                        for k in expired_keys:
                            del self._recent_texts[k]

                logger.info(
                    "[WORKER] Processing principal=%s message_type=text chars=%d qsize=%d",
                    _principal_for_log(open_id),
                    len(text),
                    self._msg_queue.qsize(),
                )
                reaction_id = self._add_reaction(msg_id, "Typing")
                threading.Thread(
                    target=self._handle_message,
                    args=(open_id, text, attachments, msg_id, reaction_id),
                    daemon=True,
                ).start()
                self._msg_queue.task_done()
            except Exception as e:
                logger.error("[WORKER] Error: %s", e, exc_info=True)

    def _ensure_worker(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        t = threading.Thread(target=self._msg_worker, daemon=True)
        t.start()
        self._worker_thread = t
        logger.info("[WORKER] Worker thread started/restarted")

    # ================================================================
    # WebSocket + Polling
    # ================================================================

    def _create_event_handler(self):
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger,
            P2CardActionTriggerResponse,
        )

        channel = self

        def on_im_message_receive(ctx):
            channel._last_ws_activity = time.time()
            channel._ws_recv_count += 1
            recv_seq = channel._ws_recv_count
            try:
                sender = ctx.event.sender
                open_id = sender.sender_id.open_id
                msg = ctx.event.message
                msg_type = msg.message_type
                msg_id = getattr(msg, "message_id", None) or ""
                chat_id = getattr(msg, "chat_id", None) or ""
                parent_id = getattr(msg, "parent_id", None) or ""
                root_id = getattr(msg, "root_id", None) or ""

                if msg_type not in ("text", "image"):
                    logger.info(
                        "[WS-RECV#%d] Skipping unsupported type: %s", recv_seq, msg_type
                    )
                    return

                content = json.loads(msg.content)
                attachments: list | None = None

                if msg_type == "text":
                    text = content.get("text", "").strip()
                    if not text:
                        logger.info("[WS-RECV#%d] Empty text, skip", recv_seq)
                        return
                    attachments = channel._attachments_for_text(
                        open_id,
                        parent_id=parent_id,
                        root_id=root_id,
                    )
                    if not channel._claim_message(msg_id):
                        logger.info("[WS-RECV#%d] Duplicate msg_id=%s, skip", recv_seq, msg_id)
                        return
                else:  # image
                    image_key = content.get("image_key", "")
                    if not image_key:
                        logger.info("[WS-RECV#%d] Image without image_key, skip", recv_seq)
                        return
                    if chat_id and open_id:
                        channel._chat_id_cache[open_id] = chat_id
                    if open_id and not channel._receive_id:
                        channel._save_open_id(open_id)
                    channel._accept_image_message(open_id, msg_id, image_key)
                    return

                if chat_id and open_id:
                    channel._chat_id_cache[open_id] = chat_id
                channel._msg_queue.put((open_id, text, msg_id, attachments))
                logger.info(
                    "[WS-RECV#%d] Queued principal=%s message_type=text chars=%d qsize=%d",
                    recv_seq,
                    _principal_for_log(open_id),
                    len(text),
                    channel._msg_queue.qsize(),
                )
            except Exception as e:
                logger.error("[WS-RECV#%d] Error: %s", recv_seq, e, exc_info=True)

        def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
            channel._last_ws_activity = time.time()
            try:
                action = data.event.action
                value = action.value or {}
                confirm_code = value.get("confirm_code", "")
                approved = value.get("approved", False)
                open_id = data.event.operator.open_id if data.event.operator else ""

                if confirm_code and open_id:
                    channel._handle_card_confirm(open_id, confirm_code, approved)
                    toast_msg = "已批准" if approved else "已拒绝"
                    return P2CardActionTriggerResponse({
                        "toast": {"type": "info", "content": toast_msg},
                    })
            except Exception as e:
                logger.error("[CARD-ACTION] Error: %s", e, exc_info=True)
            return P2CardActionTriggerResponse({
                "toast": {"type": "error", "content": "处理失败"},
            })

        def on_activity_event(_data):
            # Read receipts and reaction events are expected side effects of
            # the processing indicator. Registering a no-op prevents the SDK
            # from logging them as unhandled processor errors.
            channel._last_ws_activity = time.time()

        event_handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_im_message_receive)
            .register_p2_card_action_trigger(on_card_action)
            .register_p2_im_message_message_read_v1(on_activity_event)
            .register_p2_im_message_reaction_created_v1(on_activity_event)
            .register_p2_im_message_reaction_deleted_v1(on_activity_event)
            .build()
        )

        return event_handler

    def _get_chat_id(self, open_id: str) -> str | None:
        if open_id in self._chat_id_cache:
            return self._chat_id_cache[open_id]
        try:
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
                    .content(json.dumps({"text": "🟢 PC Assistant 已上线"}))
                    .build()
                )
                .build()
            )
            client = self._get_lark_client()
            if not self._lark_lock.acquire(timeout=5):
                return None
            try:
                resp = client.im.v1.message.create(request)
            finally:
                self._lark_lock.release()
            if resp.code == 0 and resp.data and resp.data.chat_id:
                self._chat_id_cache[open_id] = resp.data.chat_id
                return resp.data.chat_id
        except Exception as e:
            logger.debug("Get chat_id failed: %s", e)
        return None

    def _poll_missed_messages(self) -> None:
        receive_id = self._get_receive_id()
        if not receive_id:
            return

        chat_id = self._chat_id_cache.get(receive_id)
        if not chat_id:
            chat_id = self._get_chat_id(receive_id)
        if not chat_id:
            return

        try:
            from lark_oapi.api.im.v1 import ListMessageRequest

            client = self._get_lark_client()
            request = (
                ListMessageRequest.builder()
                .container_id_type("chat")
                .container_id(chat_id)
                .sort_type("ByCreateTimeDesc")
                .page_size(50)
                .build()
            )
            if not self._lark_lock.acquire(timeout=5):
                return
            try:
                resp = client.im.v1.message.list(request)
            finally:
                self._lark_lock.release()

            if resp.code != 0 or not resp.data or not resp.data.items:
                return

            messages = resp.data.items
            now = time.time()
            recovered = 0

            with self._msg_seen_lock:
                seen_ids = set(self._msg_seen.keys())

            for m in reversed(messages):
                if m.msg_type not in ("text", "image"):
                    continue
                if not m.sender or m.sender.sender_type != "user":
                    continue

                msg_id = m.message_id
                if msg_id in seen_ids:
                    continue

                msg_age = (
                    now - float(m.create_time) / 1000 if m.create_time else 9999
                )
                if msg_age > 300:
                    with self._msg_seen_lock:
                        self._msg_seen[msg_id] = now
                    continue

                has_reply = False
                msg_ctime = float(m.create_time) / 1000 if m.create_time else 0
                for rm in messages:
                    rm_ctime = float(rm.create_time) / 1000 if rm.create_time else 0
                    if (
                        rm.sender
                        and rm.sender.sender_type == "app"
                        and rm_ctime > msg_ctime
                    ):
                        has_reply = True
                        break

                if has_reply:
                    with self._msg_seen_lock:
                        self._msg_seen[msg_id] = now
                    continue

                content_str = m.body.content if m.body and m.body.content else ""
                try:
                    content = json.loads(content_str)
                except (json.JSONDecodeError, TypeError):
                    content = {}

                attachments = None
                if m.msg_type == "image":
                    image_key = content.get("image_key", "")
                    if not image_key:
                        continue
                    if self._accept_image_message(receive_id, msg_id, image_key):
                        recovered += 1
                    continue
                else:
                    text = content.get("text", "").strip() if content else content_str.strip()
                    parent_id = getattr(m, "parent_id", None) or ""
                    root_id = getattr(m, "root_id", None) or ""
                    attachments = self._attachments_for_text(
                        receive_id,
                        parent_id=parent_id,
                        root_id=root_id,
                    )
                if not text:
                    with self._msg_seen_lock:
                        self._msg_seen[msg_id] = now
                    continue

                if not self._claim_message(msg_id):
                    continue

                logger.info(
                    "[POLL] Recovered: '%s' (msg_id=%s, age=%.0fs)",
                    text,
                    msg_id,
                    msg_age,
                )
                self._msg_queue.put((receive_id, text, msg_id, attachments))
                recovered += 1

            self._last_poll_ts = now
            if recovered > 0:
                logger.info("[POLL] Recovered %d missed messages", recovered)
        except Exception as e:
            logger.warning("[POLL] Failed: %s", e)

    def _ws_loop(self) -> None:
        while self._running:
            new_loop = None
            try:
                import lark_oapi.ws.client as _ws_mod

                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                _ws_mod.loop = new_loop
                self._ws_loop_ref = new_loop

                from lark_oapi.ws import Client as WSClient

                event_handler = self._create_event_handler()
                client = WSClient(
                    app_id=self._app_id,
                    app_secret=self._app_secret,
                    event_handler=event_handler,
                    auto_reconnect=True,
                )
                # WSClient configures its own named logger during construction,
                # so enforce the level afterwards. Its INFO connection message
                # contains access_key and ticket query parameters.
                logging.getLogger("Lark").setLevel(logging.WARNING)
                _patch_ws_card_dispatch(client)
                self._last_ws_activity = time.time()
                logger.info("[WS] Starting connection (new loop)...")
                client.start()
                logger.warning("[WS] start() returned, restarting in 3s...")
            except Exception as e:
                logger.error("[WS] Connection error: %s, restarting in 3s...", e)
            finally:
                self._ws_loop_ref = None
                if new_loop is not None:
                    try:
                        pending = asyncio.all_tasks(new_loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            new_loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                        new_loop.run_until_complete(new_loop.shutdown_asyncgens())
                        new_loop.close()
                    except Exception:
                        pass
            time.sleep(3)

    def _watchdog(self) -> None:
        logger.info("[WATCHDOG] Starting initial poll in 10s...")
        time.sleep(10)
        try:
            logger.info("[WATCHDOG] Running initial poll...")
            self._poll_missed_messages()
            logger.info("[WATCHDOG] Initial poll done")
        except Exception as e:
            logger.error("[WATCHDOG] Initial poll error: %s", e, exc_info=True)

        while self._running:
            try:
                time.sleep(15)
                self._ensure_worker()

                if self._ws_loop_ref is not None:
                    if self._ws_loop_ref.is_running():
                        self._last_ws_activity = time.time()
                    else:
                        idle = time.time() - self._last_ws_activity
                        if idle > 30:
                            logger.warning(
                                "[WATCHDOG] Event loop stopped for %.0fs, forcing restart",
                                idle,
                            )
                            try:
                                self._ws_loop_ref.call_soon_threadsafe(
                                    self._ws_loop_ref.stop
                                )
                            except Exception as e:
                                logger.error("[WATCHDOG] Failed to stop loop: %s", e)
                            self._last_ws_activity = time.time()

                now = time.time()
                if now - self._last_poll_ts >= self._poll_interval:
                    self._poll_missed_messages()
            except Exception as e:
                logger.error("[WATCHDOG] Error: %s", e, exc_info=True)
