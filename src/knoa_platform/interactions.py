"""Durable, provider-neutral user interactions for active Agent turns."""
from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from knoa_agent_contracts import InteractionRequested
from knoa_platform.agent_runtime.contracts import RuntimeScope
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal

OwnerKind = Literal["conversation_turn", "task_execution"]
InteractionState = Literal[
    "pending", "resolved", "cancelled", "expired", "runtime_lost"
]


class HumanInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    interaction_id: str = Field(min_length=1, max_length=128)
    principal_id: str = Field(min_length=1, max_length=256)
    owner_kind: OwnerKind
    owner_id: str = Field(min_length=1, max_length=128)
    runtime_session_ref: str = Field(min_length=1, max_length=256)
    runtime_turn_ref: str = Field(min_length=1, max_length=256)
    runtime_interaction_id: str = Field(min_length=1, max_length=128)
    interaction_epoch: int = Field(ge=1)
    kind: Literal["user_input"] = "user_input"
    state: InteractionState
    display: dict[str, Any] = Field(default_factory=dict)
    resolution_schema: dict[str, Any] = Field(default_factory=dict)
    resolution: Any = None
    created_at: float = Field(ge=0.0)
    resolved_at: float | None = Field(default=None, ge=0.0)
    expires_at: float | None = Field(default=None, gt=0.0)
    resolved_by: str = Field(default="", max_length=256)


class HumanInteractionRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._id_factory = id_factory or (lambda: secrets.token_urlsafe(18))
        self._clock = clock
        initialize_wal(self._path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS human_interactions (
                    interaction_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    owner_kind TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    runtime_session_ref TEXT NOT NULL,
                    runtime_turn_ref TEXT NOT NULL,
                    runtime_interaction_id TEXT NOT NULL,
                    interaction_epoch INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    display_json TEXT NOT NULL,
                    resolution_schema_json TEXT NOT NULL,
                    resolution_json TEXT,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    expires_at REAL,
                    resolved_by TEXT NOT NULL,
                    UNIQUE(
                        runtime_session_ref, runtime_turn_ref,
                        runtime_interaction_id, interaction_epoch
                    )
                );
                CREATE INDEX IF NOT EXISTS human_interactions_by_owner
                    ON human_interactions(owner_kind, owner_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS human_interactions_one_pending_owner
                    ON human_interactions(owner_kind, owner_id)
                    WHERE state='pending';
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    @staticmethod
    def _json(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 128 * 1024:
            raise ValueError("HumanInteraction payload exceeds 128 KiB")
        return payload

    @staticmethod
    def _record(row: sqlite3.Row) -> HumanInteraction:
        return HumanInteraction(
            interaction_id=str(row["interaction_id"]),
            principal_id=str(row["principal_id"]),
            owner_kind=str(row["owner_kind"]),
            owner_id=str(row["owner_id"]),
            runtime_session_ref=str(row["runtime_session_ref"]),
            runtime_turn_ref=str(row["runtime_turn_ref"]),
            runtime_interaction_id=str(row["runtime_interaction_id"]),
            interaction_epoch=int(row["interaction_epoch"]),
            kind="user_input",
            state=str(row["state"]),
            display=json.loads(str(row["display_json"])),
            resolution_schema=json.loads(str(row["resolution_schema_json"])),
            resolution=(
                None
                if row["resolution_json"] is None
                else json.loads(str(row["resolution_json"]))
            ),
            created_at=float(row["created_at"]),
            resolved_at=(
                None if row["resolved_at"] is None else float(row["resolved_at"])
            ),
            expires_at=(
                None if row["expires_at"] is None else float(row["expires_at"])
            ),
            resolved_by=str(row["resolved_by"]),
        )

    def request(
        self,
        scope: RuntimeScope,
        owner_kind: OwnerKind,
        owner_id: str,
        event: InteractionRequested,
    ) -> tuple[HumanInteraction, bool]:
        if event.kind != "user_input":
            raise ValueError("Only user_input interactions are supported")
        Draft202012Validator.check_schema(event.resolution_schema)
        if event.resolution_schema.get("type") != "object":
            raise ValueError("HumanInteraction requires an object resolution schema")
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT * FROM human_interactions
                   WHERE runtime_session_ref=? AND runtime_turn_ref=?
                     AND runtime_interaction_id=? AND interaction_epoch=?""",
                (
                    event.runtime_session_ref,
                    event.runtime_turn_ref,
                    event.interaction_id,
                    event.interaction_epoch,
                ),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if (
                    record.principal_id != scope.principal_id
                    or record.owner_kind != owner_kind
                    or record.owner_id != owner_id
                    or record.display != event.display
                    or record.resolution_schema != event.resolution_schema
                ):
                    raise RuntimeError("Runtime interaction identity conflict")
                return record, False
            interaction_id = self._id_factory().strip()
            db.execute(
                """INSERT INTO human_interactions(
                       interaction_id, principal_id, owner_kind, owner_id,
                       runtime_session_ref, runtime_turn_ref,
                       runtime_interaction_id, interaction_epoch, kind, state,
                       display_json, resolution_schema_json, resolution_json,
                       created_at, resolved_at, expires_at, resolved_by
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'user_input', 'pending',
                             ?, ?, NULL, ?, NULL, ?, '')""",
                (
                    interaction_id,
                    scope.principal_id,
                    owner_kind,
                    owner_id,
                    event.runtime_session_ref,
                    event.runtime_turn_ref,
                    event.interaction_id,
                    event.interaction_epoch,
                    self._json(event.display),
                    self._json(event.resolution_schema),
                    now,
                    event.expires_at,
                ),
            )
            row = db.execute(
                "SELECT * FROM human_interactions WHERE interaction_id=?",
                (interaction_id,),
            ).fetchone()
            assert row is not None
            return self._record(row), True

    def list_owner(
        self,
        principal_id: str,
        owner_kind: OwnerKind,
        owner_id: str,
    ) -> tuple[HumanInteraction, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM human_interactions
                   WHERE principal_id=? AND owner_kind=? AND owner_id=?
                   ORDER BY created_at, interaction_id""",
                (principal_id, owner_kind, owner_id),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def resolve(
        self,
        principal_id: str,
        interaction_id: str,
        value: Any,
        *,
        resolved_by: str = "",
    ) -> tuple[HumanInteraction, bool]:
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM human_interactions
                   WHERE interaction_id=? AND principal_id=?""",
                (interaction_id, principal_id),
            ).fetchone()
            if row is None:
                raise LookupError(interaction_id)
            record = self._record(row)
            if record.state != "pending":
                return record, False
            if record.expires_at is not None and record.expires_at <= now:
                db.execute(
                    """UPDATE human_interactions SET state='expired', resolved_at=?
                       WHERE interaction_id=? AND state='pending'""",
                    (now, interaction_id),
                )
                expired = db.execute(
                    "SELECT * FROM human_interactions WHERE interaction_id=?",
                    (interaction_id,),
                ).fetchone()
                assert expired is not None
                return self._record(expired), False
            errors = sorted(
                Draft202012Validator(record.resolution_schema).iter_errors(value),
                key=lambda error: repr(tuple(error.path)),
            )
            if errors:
                raise ValueError(errors[0].message)
            db.execute(
                """UPDATE human_interactions SET state='resolved',
                       resolution_json=?, resolved_at=?, resolved_by=?
                   WHERE interaction_id=? AND state='pending'""",
                (self._json(value), now, resolved_by.strip(), interaction_id),
            )
            resolved = db.execute(
                "SELECT * FROM human_interactions WHERE interaction_id=?",
                (interaction_id,),
            ).fetchone()
            assert resolved is not None
            return self._record(resolved), True


class _InteractionHandle:
    def __init__(self, future: asyncio.Future[Any]) -> None:
        self._future = future

    async def wait(self) -> Any:
        return await self._future


class HumanInteractionService:
    def __init__(
        self,
        repository: HumanInteractionRepository,
        *,
        changed: Callable[[HumanInteraction], Awaitable[None]] | None = None,
    ) -> None:
        self._repository = repository
        self._changed = changed
        self._waiters: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    def for_owner(self, owner_kind: OwnerKind) -> ScopedInteractionPort:
        return ScopedInteractionPort(self, owner_kind)

    async def list_owner(
        self,
        principal_id: str,
        owner_kind: OwnerKind,
        owner_id: str,
    ) -> tuple[HumanInteraction, ...]:
        return await asyncio.to_thread(
            self._repository.list_owner,
            principal_id,
            owner_kind,
            owner_id,
        )

    async def begin(
        self,
        scope: RuntimeScope,
        owner_kind: OwnerKind,
        owner_id: str,
        event: InteractionRequested,
    ) -> _InteractionHandle:
        interaction, created = await asyncio.to_thread(
            self._repository.request,
            scope,
            owner_kind,
            owner_id,
            event,
        )
        if interaction.state != "pending":
            future = asyncio.get_running_loop().create_future()
            if interaction.state == "resolved":
                future.set_result(interaction.resolution)
                return _InteractionHandle(future)
            raise RuntimeError("HumanInteraction is no longer pending")
        future = asyncio.get_running_loop().create_future()
        async with self._lock:
            if interaction.interaction_id in self._waiters:
                raise RuntimeError("HumanInteraction already has a live waiter")
            self._waiters[interaction.interaction_id] = future
        if created and self._changed is not None:
            await self._changed(interaction)
        return _InteractionHandle(future)

    async def resolve(
        self,
        principal_id: str,
        interaction_id: str,
        value: Any,
        *,
        resolved_by: str = "",
    ) -> tuple[HumanInteraction, bool]:
        interaction, changed = await asyncio.to_thread(
            self._repository.resolve,
            principal_id,
            interaction_id,
            value,
            resolved_by=resolved_by,
        )
        if changed and self._changed is not None:
            await self._changed(interaction)
        async with self._lock:
            waiter = self._waiters.get(interaction_id)
            if changed and waiter is not None and not waiter.done():
                waiter.set_result(value)
                self._waiters.pop(interaction_id, None)
        return interaction, changed

    async def close(self) -> None:
        async with self._lock:
            waiters, self._waiters = self._waiters, {}
        for waiter in waiters.values():
            if not waiter.done():
                waiter.cancel()


class ScopedInteractionPort:
    def __init__(
        self,
        service: HumanInteractionService,
        owner_kind: OwnerKind,
    ) -> None:
        self._service = service
        self._owner_kind = owner_kind

    async def begin(
        self,
        scope: RuntimeScope,
        run_id: str,
        event: InteractionRequested,
    ) -> _InteractionHandle:
        return await self._service.begin(
            scope,
            self._owner_kind,
            run_id,
            event,
        )
