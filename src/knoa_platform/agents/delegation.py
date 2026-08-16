"""Governed Agent delegation backed by ordinary durable Child Tasks."""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from knoa_platform.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from knoa_platform.agents.definitions import (
    AgentDefinitionResolver,
    InvocationLimits,
    ResolvedInvocationPolicy,
)
from knoa_platform.agents.policies import InvocationPolicyRepository
from knoa_platform.artifacts import ArtifactStore
from knoa_platform.capabilities import CapabilityGateway
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal
from knoa_platform.tasks import TERMINAL_TASK_STATES, TaskOrigin, TaskService
from knoa_platform.tools.base import ToolCapability

ParentKind = Literal["conversation_turn", "task_execution"]
DelegationMode = Literal["join", "detached"]


class DelegationLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delegation_id: str
    principal_id: str
    parent_kind: ParentKind
    parent_id: str
    parent_agent_id: str
    parent_agent_digest: str
    child_agent_id: str
    child_agent_digest: str
    child_session_handle: str
    child_task_id: str
    mode: DelegationMode
    depth: int = Field(ge=1, le=8)
    invocation_policy: ResolvedInvocationPolicy
    invocation_policy_digest: str
    idempotency_key: str
    created_at: float = Field(ge=0.0)


class DelegationRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        clock=time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._clock = clock
        self._id_factory = id_factory or (
            lambda: f"delegation-{secrets.token_urlsafe(18)}"
        )
        initialize_wal(self._path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_delegations (
                    delegation_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    parent_kind TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    parent_agent_id TEXT NOT NULL,
                    parent_agent_digest TEXT NOT NULL,
                    child_agent_id TEXT NOT NULL,
                    child_agent_digest TEXT NOT NULL,
                    child_session_handle TEXT NOT NULL,
                    child_task_id TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    invocation_policy_json TEXT NOT NULL,
                    invocation_policy_digest TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(principal_id, parent_kind, parent_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_delegations_parent
                    ON agent_delegations(principal_id, parent_kind, parent_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    def create(
        self,
        *,
        principal_id: str,
        parent_kind: ParentKind,
        parent_id: str,
        parent_policy: ResolvedInvocationPolicy,
        child_session_handle: str,
        child_task_id: str,
        child_policy: ResolvedInvocationPolicy,
        mode: DelegationMode,
        depth: int,
        idempotency_key: str,
    ) -> DelegationLink:
        link = DelegationLink(
            delegation_id=self._id_factory(),
            principal_id=principal_id,
            parent_kind=parent_kind,
            parent_id=parent_id,
            parent_agent_id=parent_policy.agent_id,
            parent_agent_digest=parent_policy.agent_definition_digest,
            child_agent_id=child_policy.agent_id,
            child_agent_digest=child_policy.agent_definition_digest,
            child_session_handle=child_session_handle,
            child_task_id=child_task_id,
            mode=mode,
            depth=depth,
            invocation_policy=child_policy,
            invocation_policy_digest=child_policy.policy_digest,
            idempotency_key=idempotency_key,
            created_at=self._clock(),
        )
        with self._connect() as db:
            try:
                db.execute(
                    """INSERT INTO agent_delegations(
                           delegation_id, principal_id, parent_kind, parent_id,
                           parent_agent_id, parent_agent_digest, child_agent_id,
                           child_agent_digest, child_session_handle, child_task_id,
                           mode, depth, invocation_policy_json,
                           invocation_policy_digest, idempotency_key, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        link.delegation_id,
                        link.principal_id,
                        link.parent_kind,
                        link.parent_id,
                        link.parent_agent_id,
                        link.parent_agent_digest,
                        link.child_agent_id,
                        link.child_agent_digest,
                        link.child_session_handle,
                        link.child_task_id,
                        link.mode,
                        link.depth,
                        link.invocation_policy.model_dump_json(),
                        link.invocation_policy_digest,
                        link.idempotency_key,
                        link.created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.find_idempotent(
                    principal_id,
                    parent_kind,
                    parent_id,
                    idempotency_key,
                )
                if existing is None:
                    raise
                return existing
        return link

    def find_idempotent(
        self,
        principal_id: str,
        parent_kind: ParentKind,
        parent_id: str,
        idempotency_key: str,
    ) -> DelegationLink | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_delegations
                   WHERE principal_id=? AND parent_kind=? AND parent_id=?
                     AND idempotency_key=?""",
                (principal_id, parent_kind, parent_id, idempotency_key),
            ).fetchone()
        return None if row is None else self._link(row)

    def get_for_parent(
        self,
        principal_id: str,
        parent_kind: ParentKind,
        parent_id: str,
        delegation_id: str,
    ) -> DelegationLink:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_delegations
                   WHERE principal_id=? AND parent_kind=? AND parent_id=?
                     AND delegation_id=?""",
                (principal_id, parent_kind, parent_id, delegation_id),
            ).fetchone()
        if row is None:
            raise LookupError("Delegation not found")
        return self._link(row)

    def list_parent(
        self,
        principal_id: str,
        parent_kind: ParentKind,
        parent_id: str,
    ) -> tuple[DelegationLink, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM agent_delegations
                   WHERE principal_id=? AND parent_kind=? AND parent_id=?
                   ORDER BY created_at""",
                (principal_id, parent_kind, parent_id),
            ).fetchall()
        return tuple(self._link(row) for row in rows)

    def find_for_child_task(
        self,
        principal_id: str,
        child_task_id: str,
    ) -> DelegationLink | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM agent_delegations
                   WHERE principal_id=? AND child_task_id=?""",
                (principal_id, child_task_id),
            ).fetchone()
        return None if row is None else self._link(row)

    @staticmethod
    def _link(row: sqlite3.Row) -> DelegationLink:
        policy = ResolvedInvocationPolicy.model_validate_json(
            str(row["invocation_policy_json"])
        )
        return DelegationLink(
            delegation_id=str(row["delegation_id"]),
            principal_id=str(row["principal_id"]),
            parent_kind=str(row["parent_kind"]),
            parent_id=str(row["parent_id"]),
            parent_agent_id=str(row["parent_agent_id"]),
            parent_agent_digest=str(row["parent_agent_digest"]),
            child_agent_id=str(row["child_agent_id"]),
            child_agent_digest=str(row["child_agent_digest"]),
            child_session_handle=str(row["child_session_handle"]),
            child_task_id=str(row["child_task_id"]),
            mode=str(row["mode"]),
            depth=int(row["depth"]),
            invocation_policy=policy,
            invocation_policy_digest=str(row["invocation_policy_digest"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=float(row["created_at"]),
        )


class DelegationService:
    def __init__(
        self,
        repository: DelegationRepository,
        policies: InvocationPolicyRepository,
        sessions,
        tasks: TaskService,
        task_repository,
        conversation_repository,
        gateway: CapabilityGateway,
        artifacts: ArtifactStore,
        *,
        resolver_for: Callable[[], AgentDefinitionResolver],
        capabilities_for: Callable[[RuntimeScope], frozenset[ToolCapability]],
        installed_skills: Callable[[], frozenset[str]],
    ) -> None:
        self._repository = repository
        self._policies = policies
        self._sessions = sessions
        self._tasks = tasks
        self._task_repository = task_repository
        self._conversations = conversation_repository
        self._gateway = gateway
        self._artifacts = artifacts
        self._resolver_for = resolver_for
        self._capabilities_for = capabilities_for
        self._installed_skills = installed_skills
        self._spawn_lock = asyncio.Lock()

    async def spawn(
        self,
        scope: RuntimeScope,
        parent_id: str,
        *,
        target_agent_id: str,
        goal: str,
        context: dict,
        requested_capabilities: frozenset[str],
        requested_tools: frozenset[str],
        requested_skills: frozenset[str],
        deadline_seconds: float,
        mode: DelegationMode,
        idempotency_key: str,
    ) -> DelegationLink:
        async with self._spawn_lock:
            return await self._spawn_unlocked(
                scope,
                parent_id,
                target_agent_id=target_agent_id,
                goal=goal,
                context=context,
                requested_capabilities=requested_capabilities,
                requested_tools=requested_tools,
                requested_skills=requested_skills,
                deadline_seconds=deadline_seconds,
                mode=mode,
                idempotency_key=idempotency_key,
            )

    async def _spawn_unlocked(
        self,
        scope: RuntimeScope,
        parent_id: str,
        *,
        target_agent_id: str,
        goal: str,
        context: dict,
        requested_capabilities: frozenset[str],
        requested_tools: frozenset[str],
        requested_skills: frozenset[str],
        deadline_seconds: float,
        mode: DelegationMode,
        idempotency_key: str,
    ) -> DelegationLink:
        parent_kind = self.parent_kind(scope.principal_id, parent_id)
        if parent_kind == "task_execution" and mode == "join":
            raise PermissionError("Task parents may only create detached children")
        existing = self._repository.find_idempotent(
            scope.principal_id,
            parent_kind,
            parent_id,
            idempotency_key,
        )
        if existing is not None:
            child = await self._tasks.get(
                scope.principal_id,
                existing.child_task_id,
            )
            if child.phase == "delegation_staged":
                await self._tasks.activate_staged(
                    scope.principal_id,
                    existing.child_task_id,
                )
            return existing
        parent_policy = self._policies.get(parent_id)
        if target_agent_id not in parent_policy.delegation_targets:
            raise PermissionError(
                "Target Agent is not allowed by parent delegation policy"
            )
        links = self._repository.list_parent(
            scope.principal_id,
            parent_kind,
            parent_id,
        )
        if len(links) >= parent_policy.limits.max_children:
            raise PermissionError("Parent child limit reached")
        active_children = 0
        for current in links:
            try:
                task = await self._tasks.get(
                    scope.principal_id,
                    current.child_task_id,
                )
            except LookupError:
                active_children += 1
            else:
                if task.state not in TERMINAL_TASK_STATES:
                    active_children += 1
        if active_children >= parent_policy.limits.max_parallel_children:
            raise PermissionError("Parent parallel child limit reached")
        if parent_policy.delegation_max_depth < 1:
            raise PermissionError("Parent delegation depth limit reached")
        if mode == "join" and target_agent_id == parent_policy.agent_id:
            raise PermissionError("Join delegation requires a different target Agent")
        if deadline_seconds <= 0 or (
            parent_policy.delegation_max_deadline_seconds
            and deadline_seconds > parent_policy.delegation_max_deadline_seconds
        ):
            raise PermissionError("Delegation deadline exceeds parent policy")

        resolver = self._resolver_for()
        child_delegation = resolver.profile(target_agent_id).delegation
        principal_capabilities = self._capabilities_for(scope)
        available_tools = self._gateway.available_tool_names(principal_capabilities)
        child_policy = resolver.resolve_policy(
            target_agent_id,
            invocation_kind="delegate",
            caller_id=parent_policy.agent_id,
            principal_capabilities=frozenset(
                capability.value for capability in principal_capabilities
            ),
            available_tools=available_tools,
            installed_skills=self._installed_skills(),
            requested_tools=requested_tools,
            requested_skills=requested_skills,
            parent=parent_policy,
            artifact_ids=frozenset(context.get("artifact_ids") or ()),
            limits=InvocationLimits(
                deadline_seconds=deadline_seconds,
                max_gateway_tool_calls=parent_policy.limits.max_gateway_tool_calls,
                max_artifact_bytes=parent_policy.limits.max_artifact_bytes,
                max_children=child_delegation.max_children,
                max_parallel_children=child_delegation.max_parallel_children,
            ),
        )
        if not requested_capabilities <= child_policy.platform_capabilities:
            raise PermissionError("Requested capabilities exceed child policy")
        child_policy = child_policy.model_copy(
            update={
                "platform_capabilities": (
                    child_policy.platform_capabilities & requested_capabilities
                )
            }
        )
        parent_artifact_ids = tuple(sorted(child_policy.artifact_ids))
        artifact_bytes = sum(
            int(
                self._artifacts.metadata(
                    scope.session_handle,
                    artifact_id,
                )["size"]
            )
            for artifact_id in parent_artifact_ids
        )
        if artifact_bytes > child_policy.limits.max_artifact_bytes:
            raise PermissionError("Delegation Artifacts exceed the invocation budget")
        child_scope = self._sessions.create(
            scope.principal_id,
            activate=False,
            agent_id=target_agent_id,
        )
        try:
            shared_artifact_ids = frozenset(
                str(
                    self._artifacts.share_to_session(
                        scope.session_handle,
                        child_scope.session_handle,
                        artifact_id,
                    )["artifact_id"]
                )
                for artifact_id in parent_artifact_ids
            )
        except BaseException:
            self._artifacts.cleanup_session(child_scope.session_handle)
            raise
        child_policy = child_policy.model_copy(
            update={"artifact_ids": shared_artifact_ids}
        )
        context_text = json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        child_goal = goal.strip()
        if context:
            child_goal += f"\n\n<delegation_context>{context_text}</delegation_context>"
        attachments = tuple(
            ArtifactAttachment(artifact_id=artifact_id)
            for artifact_id in sorted(child_policy.artifact_ids)
        )
        try:
            child = await self._tasks.create(
                child_scope,
                client_request_id=f"delegation:{idempotency_key}",
                goal=child_goal,
                attachments=attachments,
                tools_enabled=bool(child_policy.allowed_platform_tools),
                parent_task_id=parent_id if parent_kind == "task_execution" else "",
                origin=TaskOrigin.AGENT,
                agent_id=target_agent_id,
                defer_start=True,
                staged=True,
            )
        except BaseException:
            self._artifacts.cleanup_session(child_scope.session_handle)
            raise
        try:
            self._policies.record(
                child.task_id,
                scope.principal_id,
                child.session_handle,
                child_policy,
            )
            parent_link = (
                self._repository.find_for_child_task(scope.principal_id, parent_id)
                if parent_kind == "task_execution"
                else None
            )
            link = self._repository.create(
                principal_id=scope.principal_id,
                parent_kind=parent_kind,
                parent_id=parent_id,
                parent_policy=parent_policy,
                child_session_handle=child.session_handle,
                child_task_id=child.task_id,
                child_policy=child_policy,
                mode=mode,
                depth=(parent_link.depth + 1 if parent_link is not None else 1),
                idempotency_key=idempotency_key,
            )
            await self._tasks.activate_staged(scope.principal_id, child.task_id)
            return link
        except BaseException:
            try:
                await self._tasks.cancel(
                    scope.principal_id,
                    child.task_id,
                    reason="Delegation activation failed",
                )
            finally:
                self._artifacts.cleanup_session(child_scope.session_handle)
            raise

    async def recover_staged(self) -> None:
        """Activate fully linked children and fail closed on incomplete staging."""

        for task in await self._tasks.list_staged():
            link = self._repository.find_for_child_task(
                task.principal_id,
                task.task_id,
            )
            try:
                policy = self._policies.get(task.task_id)
            except LookupError:
                policy = None
            if (
                link is not None
                and policy is not None
                and policy.policy_digest == link.invocation_policy_digest
            ):
                await self._tasks.activate_staged(task.principal_id, task.task_id)
                continue
            await self._tasks.cancel(
                task.principal_id,
                task.task_id,
                reason="Delegation staging was incomplete during Core recovery",
            )
            self._artifacts.cleanup_session(task.session_handle)

    async def result(
        self,
        scope: RuntimeScope,
        parent_id: str,
        delegation_id: str,
    ) -> dict:
        parent_kind = self.parent_kind(scope.principal_id, parent_id)
        link = self._repository.get_for_parent(
            scope.principal_id,
            parent_kind,
            parent_id,
            delegation_id,
        )
        task = await self._tasks.get(scope.principal_id, link.child_task_id)
        return {
            "delegation_id": link.delegation_id,
            "child_task_id": link.child_task_id,
            "mode": link.mode,
            "status": task.state.value,
            "summary": task.final_summary,
            "warnings": ([task.failure_code] if task.failure_code else []),
        }

    async def cancel(
        self,
        scope: RuntimeScope,
        parent_id: str,
        delegation_id: str,
    ) -> dict:
        parent_kind = self.parent_kind(scope.principal_id, parent_id)
        link = self._repository.get_for_parent(
            scope.principal_id,
            parent_kind,
            parent_id,
            delegation_id,
        )
        await self._tasks.cancel(
            scope.principal_id,
            link.child_task_id,
            reason="Cancelled by parent Agent",
        )
        return await self.result(scope, parent_id, delegation_id)

    def parent_kind(self, principal_id: str, parent_id: str) -> ParentKind:
        try:
            self._task_repository.get(principal_id, parent_id)
            return "task_execution"
        except LookupError:
            self._conversations.get(principal_id, parent_id)
            return "conversation_turn"
