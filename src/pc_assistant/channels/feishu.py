from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
import warnings
from typing import Any

from pc_assistant.channels.base import ChannelBase

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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_RECEIVED_OPEN_ID_FILE = os.path.join(os.path.dirname(__file__), ".feishu_open_id")


class FeishuChannel(ChannelBase):
    name = "feishu"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        receive_id: str = "",
        receive_id_type: str = "open_id",
    ) -> None:
        self._app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self._app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._receive_id = receive_id or os.environ.get("FEISHU_RECEIVE_ID", "")
        self._receive_id_type = receive_id_type or os.environ.get(
            "FEISHU_RECEIVE_ID_TYPE", "open_id"
        )

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

        self._conversations: dict[str, list[dict[str, Any]]] = {}
        self._conversation_lock = threading.Lock()

        self._pending_confirm: dict[str, dict[str, Any]] = {}
        self._pending_confirm_lock = threading.Lock()

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
        if os.path.exists(_RECEIVED_OPEN_ID_FILE):
            try:
                with open(_RECEIVED_OPEN_ID_FILE, "r") as f:
                    return f.read().strip()
            except Exception:
                pass
        return ""

    def _save_open_id(self, open_id: str) -> None:
        try:
            with open(_RECEIVED_OPEN_ID_FILE, "w") as f:
                f.write(open_id)
            logger.info("Saved open_id: %s", open_id)
        except Exception as e:
            logger.warning("Failed to save open_id: %s", e)

    # ================================================================
    # Message Sending
    # ================================================================

    def _add_reaction(self, msg_id: str, emoji_type: str = "OK") -> None:
        if not msg_id:
            return
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
                return
            try:
                client.im.v1.message_reaction.create(request)
            finally:
                self._lark_lock.release()
        except Exception as e:
            logger.debug("Reaction failed (non-critical): %s", e)

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
                logger.info("[SEND-TEXT] OK to %s (%d chars)", open_id, len(text))
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
                logger.info("[SEND-CARD] OK to %s", open_id)
                return True
            else:
                logger.error("[SEND-CARD] FAILED code=%s msg=%s", resp.code, resp.msg)
                return False
        except Exception as e:
            logger.error("[SEND-CARD] Exception: %s", e, exc_info=True)
            return False

    # ================================================================
    # Message Handling
    # ================================================================

    def _handle_message(self, open_id: str, text: str) -> None:
        if self._agent_loop is None:
            self._send_text(open_id, "❌ Agent 未就绪")
            return

        text_stripped = text.strip()
        if not text_stripped:
            return

        with self._pending_confirm_lock:
            pending = self._pending_confirm.get(open_id)
            if pending is not None:
                self._handle_confirm(open_id, text_stripped, pending)
                return

        future = asyncio.run_coroutine_threadsafe(
            self._process_with_agent(open_id, text_stripped),
            self._agent_loop,
        )
        try:
            future.result(timeout=180)
        except asyncio.TimeoutError:
            self._send_text(open_id, "❌ 处理超时，请简化问题重试")
        except Exception as e:
            logger.error("[HANDLE] Agent processing failed: %s", e, exc_info=True)
            self._send_text(open_id, f"❌ 处理失败: {e}")

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
            if time.time() - pending["ts"] > 300:
                self._send_text(open_id, "❌ 确认码已过期(5分钟)，请重新操作")
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
            self._send_text(open_id, "✅ 操作已取消")
        else:
            with self._pending_confirm_lock:
                self._pending_confirm.pop(open_id, None)
            future = asyncio.run_coroutine_threadsafe(
                self._process_with_agent(open_id, text),
                self._agent_loop,
            )
            try:
                future.result(timeout=180)
            except Exception as e:
                self._send_text(open_id, f"❌ 处理失败: {e}")

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
                expired = [k for k, v in self._pending_confirm.items() if now - v["ts"] > 300]
                for k in expired:
                    del self._pending_confirm[k]
        return self._send_text(
            open_id,
            f"⚠️ **请确认操作**\n{action_desc}\n\n发送 `确认 {code}` 执行，`取消` 放弃，5分钟内有效",
        )

    async def _process_with_agent(self, open_id: str, text: str) -> None:
        if self._agent is None:
            self._send_text(open_id, "❌ Agent 未初始化")
            return

        conv = self._get_conversation(open_id)
        conv.append({"role": "user", "content": text})

        original_conversation = self._agent._conversation
        original_system_prompt = self._agent._system_prompt

        try:
            from pc_assistant.context.conversation import ConversationManager

            feishu_conv = ConversationManager()
            feishu_conv.set_system_context(original_system_prompt)
            for msg in conv:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    feishu_conv.add_user(content)
                elif role == "assistant":
                    feishu_conv.add_assistant_final(content)

            self._agent._conversation = feishu_conv

            tool_calls_info: list[str] = []
            final_answer = ""
            error_msg = ""

            async for event in self._agent.run(text):
                if event.type == "tool_call" and not event.blocked:
                    tool_name = event.tool_name
                    tool_args = event.tool_args
                    args_brief = json.dumps(tool_args, ensure_ascii=False)[:80]
                    tool_calls_info.append(f"🔧 {tool_name}({args_brief})")
                elif event.type == "tool_result":
                    pass
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
                response = f"{tools_summary}\n\n{final_answer}"
            elif final_answer:
                response = final_answer
            elif error_msg:
                response = f"❌ {error_msg}"
            else:
                response = "⚠️ 未获得有效回复，请重试"

            conv.append({"role": "assistant", "content": response})

            with self._conversation_lock:
                if len(self._conversations.get(open_id, [])) > 40:
                    self._conversations[open_id] = self._conversations[open_id][-20:]

            self._send_long_text(open_id, response)

        except Exception as e:
            logger.error("[PROCESS] Agent error: %s", e, exc_info=True)
            self._send_text(open_id, f"❌ 处理出错: {e}")
        finally:
            self._agent._conversation = original_conversation

    def _send_long_text(self, open_id: str, text: str) -> None:
        max_len = 2000
        if len(text) <= max_len:
            self._send_text(open_id, text)
        else:
            for i in range(0, len(text), max_len):
                self._send_text(open_id, text[i : i + max_len])

    def _get_conversation(self, open_id: str) -> list[dict[str, Any]]:
        with self._conversation_lock:
            if open_id not in self._conversations:
                self._conversations[open_id] = []
            return self._conversations[open_id]

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
                open_id, text, msg_id = item

                self._add_reaction(msg_id, "OK")

                if msg_id:
                    with self._msg_seen_lock:
                        if msg_id in self._msg_seen:
                            logger.info("[WORKER] Duplicate msg_id=%s, skip", msg_id)
                            self._msg_queue.task_done()
                            continue
                        self._msg_seen[msg_id] = time.time()
                        if len(self._msg_seen) > 1000:
                            now = time.time()
                            expired = [
                                k for k, v in self._msg_seen.items() if now - v > 300
                            ]
                            for k in expired:
                                del self._msg_seen[k]

                if open_id and not self._receive_id:
                    self._save_open_id(open_id)

                now = time.time()
                with self._recent_texts_lock:
                    dedup_key = f"{open_id}:{text}"
                    last_time = self._recent_texts.get(dedup_key, 0)
                    if now - last_time < 30:
                        logger.info(
                            "[WORKER] Duplicate text within 30s: '%s', skip", text
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
                    "[WORKER] Processing: '%s' from %s (qsize=%d)",
                    text,
                    open_id,
                    self._msg_queue.qsize(),
                )
                threading.Thread(
                    target=self._handle_message,
                    args=(open_id, text),
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

                if msg_type != "text":
                    logger.info(
                        "[WS-RECV#%d] Skipping non-text: %s", recv_seq, msg_type
                    )
                    return

                content = json.loads(msg.content)
                text = content.get("text", "").strip()
                if not text:
                    logger.info("[WS-RECV#%d] Empty text, skip", recv_seq)
                    return

                msg_id = getattr(msg, "message_id", None) or ""
                chat_id = getattr(msg, "chat_id", None) or ""
                if chat_id and open_id:
                    channel._chat_id_cache[open_id] = chat_id
                channel._msg_queue.put((open_id, text, msg_id))
                logger.info(
                    "[WS-RECV#%d] Queued: '%s' from %s (qsize=%d)",
                    recv_seq,
                    text,
                    open_id,
                    channel._msg_queue.qsize(),
                )
            except Exception as e:
                logger.error("[WS-RECV#%d] Error: %s", recv_seq, e, exc_info=True)

        event_handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_im_message_receive)
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
                if m.msg_type != "text":
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
                    text = content.get("text", "").strip()
                except (json.JSONDecodeError, TypeError):
                    text = content_str.strip()
                if not text:
                    with self._msg_seen_lock:
                        self._msg_seen[msg_id] = now
                    continue

                logger.info(
                    "[POLL] Recovered: '%s' (msg_id=%s, age=%.0fs)",
                    text,
                    msg_id,
                    msg_age,
                )
                self._msg_queue.put((receive_id, text, msg_id))
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
