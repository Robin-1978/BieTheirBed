"""Shared session, owner binding and Core client management for IM channels."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from pathlib import Path

from knoa_platform.config import AppConfig
from knoa_platform.runtime import RuntimePaths
from knoa_platform.service.core_client import CoreClient
from knoa_platform.service.credentials import (
    issue_principal_credential,
    resolve_local_service_token,
)
from knoa_platform.tasks import TaskEvent

logger = logging.getLogger(__name__)


class ChannelSessionMixin:
    """Owner binding, session persistence and per-principal Core clients."""

    name: str
    _config: AppConfig
    _paths: RuntimePaths
    _receive_id: str
    _binding_path: Path
    _sessions_path: Path
    _notification_cursors_path: Path
    _notification_intent_cursors_path: Path
    _outbox: Path
    _binding_lock: threading.RLock
    _clients: dict[str, CoreClient]
    _client_locks: dict[str, asyncio.Lock]
    _main_loop: asyncio.AbstractEventLoop | None
    _running: bool
    _sessions: dict[str, str]
    _session_users: dict[str, str]
    _notification_cursors: dict[str, int]
    _notification_intent_cursors: dict[str, int]

    def _init_channel_storage(self, channel_prefix: str) -> None:
        """Configure persisted paths for a channel without hardcoding provider names."""
        self._binding_path = self._paths.data / f"{channel_prefix}_open_id"
        self._sessions_path = self._paths.data / f"{channel_prefix}_sessions.json"
        self._notification_cursors_path = (
            self._paths.data / f"{channel_prefix}_notification_cursors.json"
        )
        self._notification_intent_cursors_path = (
            self._paths.data / f"{channel_prefix}_notification_intent_cursors.json"
        )
        self._outbox = self._paths.cache / f"{channel_prefix}-outbox"

    def _init_channel_runtime_state(self) -> None:
        self._binding_lock = threading.RLock()
        self._clients = {}
        self._client_locks = {}
        self._main_loop = None
        self._running = False
        self._sessions = {}
        self._session_users = {}
        self._notification_cursors = {}
        self._notification_intent_cursors = {}

    def _current_receive_id(self) -> str:
        if self._receive_id:
            return self._receive_id
        try:
            return self._binding_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _check_owner(self, principal_id: str) -> bool:
        """Return whether the sender is allowed to use this channel instance."""
        normalized = principal_id.strip()
        if not normalized:
            return False
        current = self._current_receive_id()
        return not current or current == normalized

    def _save_binding(self, principal_id: str) -> bool:
        """Bind the first channel owner and never let another sender replace it."""
        normalized = principal_id.strip()
        if not normalized:
            return False
        with self._binding_lock:
            current = self._current_receive_id()
            if current and current != normalized:
                return False
            self._receive_id = normalized
            self._binding_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._binding_path.write_text(normalized, encoding="utf-8")
            self._binding_path.chmod(0o600)
        return True

    async def _session_for(self, principal_id: str) -> str:
        session = self._sessions.get(principal_id)
        if session:
            self._session_users[session] = principal_id
            return session
        session = await (await self._client_for(principal_id)).create_session()
        self._bind_session(principal_id, session)
        return session

    def _bind_session(self, principal_id: str, session: str) -> None:
        previous = self._sessions.get(principal_id)
        if previous:
            self._session_users.pop(previous, None)
        self._sessions[principal_id] = session
        self._session_users[session] = principal_id
        self._save_sessions()
        self._ensure_principal_watcher(principal_id)

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
                    session: principal_id
                    for principal_id, session in self._sessions.items()
                }
        except FileNotFoundError:
            return
        except Exception:
            logger.warning(
                "Ignoring invalid %s session mapping",
                self.name,
                exc_info=True,
            )

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

    def _load_notification_cursors(self) -> None:
        try:
            data = json.loads(
                self._notification_cursors_path.read_text(encoding="utf-8")
            )
            if isinstance(data, dict):
                self._notification_cursors = {
                    str(principal_id): int(cursor)
                    for principal_id, cursor in data.items()
                    if str(principal_id) and int(cursor) >= 0
                }
        except FileNotFoundError:
            return
        except Exception:
            logger.warning(
                "Ignoring invalid %s notification cursors",
                self.name,
                exc_info=True,
            )

    def _save_notification_cursors(self) -> None:
        path = self._notification_cursors_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(
                self._notification_cursors,
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)

    def _load_notification_intent_cursors(self) -> None:
        try:
            data = json.loads(
                self._notification_intent_cursors_path.read_text(encoding="utf-8")
            )
            if isinstance(data, dict):
                self._notification_intent_cursors = {
                    str(principal_id): int(cursor)
                    for principal_id, cursor in data.items()
                    if str(principal_id) and int(cursor) >= 0
                }
        except FileNotFoundError:
            return
        except Exception:
            logger.warning(
                "Ignoring invalid %s notification intent cursors",
                self.name,
                exc_info=True,
            )

    def _save_notification_intent_cursors(self) -> None:
        path = self._notification_intent_cursors_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(self._notification_intent_cursors, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)

    async def _client_for(self, principal_id: str) -> CoreClient:
        lock = self._client_locks.setdefault(principal_id, asyncio.Lock())
        async with lock:
            current = self._clients.get(principal_id)
            if current is not None and current.is_connected:
                return current
            if current is not None:
                await current.disconnect()
            signing_key = resolve_local_service_token(self._paths)
            credential = issue_principal_credential(
                signing_key,
                self._config.owner_principal_id,
            )

            async def confirm(message: TaskEvent) -> bool:
                return await self._confirm_tool(principal_id, message)

            client = await CoreClient.connect(
                f"ws://{self._config.service_host}:{self._config.service_port}",
                credential,
                approval_handler=confirm,
                max_buffered_task_events=4096,
            )
            self._clients[principal_id] = client
            return client

    async def _shutdown_shared_resources(self) -> None:
        """Stop watchers, clear pending UI state and disconnect Core clients."""
        self._running = False
        watchers, self._principal_watchers = (
            tuple(self._principal_watchers.values()),
            {},
        )
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        self._principal_watcher_started_at.clear()
        self._foreground_task_ids.clear()
        self._active_chat_turn_ids.clear()
        self._background_approval_decisions.clear()
        self._pending_text_interactions.clear()
        with self._pending_confirmation_lock:
            pending_confirmations = tuple(self._pending_confirmations.values())
            self._pending_confirmations.clear()
            for pending in pending_confirmations:
                pending.resolved = True
        for pending in pending_confirmations:
            self._schedule_confirmation_result(pending, False)
        clients, self._clients = tuple(self._clients.values()), {}
        await asyncio.gather(
            *(client.disconnect() for client in clients),
            return_exceptions=True,
        )


__all__ = ["ChannelSessionMixin"]
