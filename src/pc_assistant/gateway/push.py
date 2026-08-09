"""Gateway-owned mobile Push registration and standard Task event delivery."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from pc_assistant.gateway.core import GatewayCoreBridge
from pc_assistant.gateway.storage import prepare_owner_only_database
from pc_assistant.sqlite_schema import require_exact_table, require_index_columns
from pc_assistant.tasks import PrincipalTaskEvent


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayPushRegistration:
    device_id: str
    principal_id: str
    provider: str
    token: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class GatewayPushMessage:
    category: str
    task_id: str
    approval_id: str
    title: str
    body: str


class PushTransport(Protocol):
    async def send(
        self,
        registration: GatewayPushRegistration,
        message: GatewayPushMessage,
    ) -> None: ...


class ExpoPushTransport:
    def __init__(
        self,
        endpoint: str = "https://exp.host/--/api/v2/push/send",
        *,
        timeout: float = 15.0,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    async def send(
        self,
        registration: GatewayPushRegistration,
        message: GatewayPushMessage,
    ) -> None:
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            response = await client.post(
                self._endpoint,
                json={
                    "to": registration.token,
                    "title": message.title,
                    "body": message.body,
                    "sound": "default",
                    "data": {
                        "category": message.category,
                        "task_id": message.task_id,
                        "approval_id": message.approval_id,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("status") == "error":
                raise RuntimeError("Push provider rejected the notification")


class GatewayPushRepository:
    def __init__(self, db_path: str | Path, *, clock=time.time) -> None:
        self._db_path = prepare_owner_only_database(
            db_path,
            label="Gateway Push database",
        )
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gateway_push_registrations (
                    device_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gateway_push_cursors (
                    principal_id TEXT PRIMARY KEY,
                    last_event_id INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS gateway_push_principal_idx
                ON gateway_push_registrations(principal_id, updated_at DESC);
                """
            )
            require_exact_table(
                connection,
                "gateway_push_registrations",
                (
                    ("device_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("provider", "TEXT", True, None, 0),
                    ("token", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Gateway Push registration",
            )
            require_exact_table(
                connection,
                "gateway_push_cursors",
                (
                    ("principal_id", "TEXT", False, None, 1),
                    ("last_event_id", "INTEGER", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Gateway Push cursor",
            )
            require_index_columns(
                connection,
                "gateway_push_principal_idx",
                ("principal_id", "updated_at"),
                label="Gateway Push principal index",
            )

    def register(
        self,
        device_id: str,
        principal_id: str,
        provider: str,
        token: str,
    ) -> GatewayPushRegistration:
        device = self._text(device_id, 128)
        principal = self._text(principal_id, 256)
        normalized_provider = self._text(provider, 32)
        if normalized_provider != "expo":
            raise ValueError("Unsupported Push provider")
        normalized_token = self._text(token, 512)
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gateway_push_registrations (
                    device_id, principal_id, provider, token, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    principal_id=excluded.principal_id,
                    provider=excluded.provider,
                    token=excluded.token,
                    updated_at=excluded.updated_at
                """,
                (device, principal, normalized_provider, normalized_token, now, now),
            )
            row = connection.execute(
                "SELECT * FROM gateway_push_registrations WHERE device_id = ?",
                (device,),
            ).fetchone()
        assert row is not None
        return self._registration(row)

    def unregister(self, principal_id: str, device_id: str) -> bool:
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM gateway_push_registrations "
                "WHERE principal_id = ? AND device_id = ?",
                (self._text(principal_id, 256), self._text(device_id, 128)),
            )
        return deleted.rowcount == 1

    def list_for_principal(
        self,
        principal_id: str,
    ) -> tuple[GatewayPushRegistration, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM gateway_push_registrations "
                "WHERE principal_id = ? ORDER BY updated_at DESC",
                (self._text(principal_id, 256),),
            ).fetchall()
        return tuple(self._registration(row) for row in rows)

    def cursor(self, principal_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_event_id FROM gateway_push_cursors WHERE principal_id = ?",
                (self._text(principal_id, 256),),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def save_cursor(self, principal_id: str, event_id: int) -> None:
        if event_id < 0:
            raise ValueError("Push cursor must be non-negative")
        now = float(self._clock())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gateway_push_cursors (
                    principal_id, last_event_id, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(principal_id) DO UPDATE SET
                    last_event_id=MAX(last_event_id, excluded.last_event_id),
                    updated_at=excluded.updated_at
                """,
                (self._text(principal_id, 256), event_id, now),
            )

    @staticmethod
    def _text(value: str, limit: int) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > limit or any(
            character in normalized for character in "\r\n"
        ):
            raise ValueError("Push registration value is invalid")
        return normalized

    @staticmethod
    def _registration(row: sqlite3.Row) -> GatewayPushRegistration:
        return GatewayPushRegistration(
            device_id=str(row["device_id"]),
            principal_id=str(row["principal_id"]),
            provider=str(row["provider"]),
            token=str(row["token"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


class GatewayPushDispatcher:
    def __init__(
        self,
        principal_id: str,
        core: GatewayCoreBridge,
        repository: GatewayPushRepository,
        transport: PushTransport,
        *,
        reconnect_seconds: float = 3.0,
    ) -> None:
        self._principal_id = principal_id
        self._core = core
        self._repository = repository
        self._transport = transport
        self._reconnect_seconds = reconnect_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="knoa-push-dispatcher")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            try:
                after_id = await asyncio.to_thread(
                    self._repository.cursor,
                    self._principal_id,
                )
                async for feed in self._core.principal_task_events(
                    self._principal_id,
                    after_id=after_id,
                ):
                    await self._deliver(feed)
                    await asyncio.to_thread(
                        self._repository.save_cursor,
                        self._principal_id,
                        feed.feed_event_id,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Gateway Push event subscription failed", exc_info=True)
                await asyncio.sleep(self._reconnect_seconds)

    async def _deliver(self, feed: PrincipalTaskEvent) -> None:
        message = self.message_for(feed)
        if message is None:
            return
        registrations = await asyncio.to_thread(
            self._repository.list_for_principal,
            self._principal_id,
        )
        results = await asyncio.gather(
            *(self._transport.send(registration, message) for registration in registrations),
            return_exceptions=True,
        )
        for registration, result in zip(registrations, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "Gateway Push delivery failed device=%s category=%s",
                    registration.device_id,
                    message.category,
                )

    @staticmethod
    def message_for(feed: PrincipalTaskEvent) -> GatewayPushMessage | None:
        event = feed.event
        mapping = {
            "approval_requested": ("approval", "需要确认", "有一步操作等待你的确认"),
            "completed": ("task_completed", "任务完成", "小诺已经完成任务"),
            "failed": ("task_failed", "任务失败", "任务遇到问题，请查看详情"),
            "cancelled": ("task_cancelled", "任务已停止", "任务已经停止"),
        }
        selected = mapping.get(event.event_type)
        if selected is None:
            return None
        category, title, body = selected
        return GatewayPushMessage(
            category=category,
            task_id=event.task_id,
            approval_id=event.payload.approval_id,
            title=title,
            body=body,
        )
