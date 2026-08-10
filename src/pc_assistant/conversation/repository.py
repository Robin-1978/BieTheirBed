"""SQLite persistence for Conversation ChatTurns and durable side effects."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pc_assistant.agent_runtime.contracts import ArtifactAttachment, RuntimeScope
from pc_assistant.agent_runtime.tool_step import ProposedToolCall, ToolStepResult
from pc_assistant.artifacts import ArtifactRef
from pc_assistant.conversation.models import (
    ChatApproval,
    ChatToolStep,
    ChatTurn,
    ChatTurnState,
    TERMINAL_CHAT_TURN_STATES,
)
from pc_assistant.exceptions import SessionNotFoundError
from pc_assistant.sqlite_schema import require_exact_table, require_foreign_keys
from pc_assistant.tools.base import ToolPolicy


class ChatTurnNotFoundError(LookupError):
    pass


class ChatTurnConflictError(RuntimeError):
    pass


class ConversationRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        turn_id_factory: Callable[[], str] | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
        detail_retention_seconds: float = 30 * 24 * 60 * 60,
    ) -> None:
        if not 60 <= detail_retention_seconds <= 10 * 365 * 24 * 60 * 60:
            raise ValueError(
                "Conversation detail retention must be between 60 seconds and 10 years"
            )
        self._path = Path(db_path).expanduser().resolve()
        self._turn_id_factory = turn_id_factory or (lambda: secrets.token_urlsafe(18))
        self._approval_id_factory = approval_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )
        self._clock = clock
        self._detail_retention_seconds = float(detail_retention_seconds)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    turn_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    session_handle TEXT NOT NULL
                        REFERENCES runtime_sessions(session_handle) ON DELETE CASCADE,
                    client_request_id TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    tools_enabled INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    reasoning TEXT NOT NULL,
                    content TEXT NOT NULL,
                    final_output TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL,
                    revision INTEGER NOT NULL,
                    UNIQUE(principal_id, client_request_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_tool_steps (
                    step_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL
                        REFERENCES conversation_turns(turn_id) ON DELETE CASCADE,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(turn_id, tool_call_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_approvals (
                    approval_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL
                        REFERENCES conversation_turns(turn_id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    resolved_by TEXT NOT NULL,
                    UNIQUE(turn_id, step_id)
                );
                CREATE INDEX IF NOT EXISTS conversation_turns_by_session
                    ON conversation_turns(session_handle, created_at, turn_id);
                CREATE INDEX IF NOT EXISTS conversation_approvals_by_turn_state
                    ON conversation_approvals(turn_id, state, created_at);
                """
            )
            require_exact_table(
                db,
                "conversation_turns",
                (
                    ("turn_id", "TEXT", False, None, 1),
                    ("principal_id", "TEXT", True, None, 0),
                    ("session_handle", "TEXT", True, None, 0),
                    ("client_request_id", "TEXT", True, None, 0),
                    ("user_input", "TEXT", True, None, 0),
                    ("attachments_json", "TEXT", True, None, 0),
                    ("tools_enabled", "INTEGER", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("reasoning", "TEXT", True, None, 0),
                    ("content", "TEXT", True, None, 0),
                    ("final_output", "TEXT", True, None, 0),
                    ("artifacts_json", "TEXT", True, None, 0),
                    ("failure_code", "TEXT", True, None, 0),
                    ("cancel_requested", "INTEGER", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                    ("finished_at", "REAL", False, None, 0),
                    ("revision", "INTEGER", True, None, 0),
                ),
                label="Conversation Turn",
            )
            require_foreign_keys(
                db,
                "conversation_turns",
                (("runtime_sessions", "session_handle", "session_handle", "NO ACTION", "CASCADE"),),
                label="Conversation Turn",
            )
            require_exact_table(
                db,
                "conversation_tool_steps",
                (
                    ("step_id", "TEXT", False, None, 1),
                    ("turn_id", "TEXT", True, None, 0),
                    ("tool_call_id", "TEXT", True, None, 0),
                    ("tool_name", "TEXT", True, None, 0),
                    ("arguments_json", "TEXT", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("result_json", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("updated_at", "REAL", True, None, 0),
                ),
                label="Conversation Tool Step",
            )
            require_foreign_keys(
                db,
                "conversation_tool_steps",
                (("conversation_turns", "turn_id", "turn_id", "NO ACTION", "CASCADE"),),
                label="Conversation Tool Step",
            )
            require_exact_table(
                db,
                "conversation_approvals",
                (
                    ("approval_id", "TEXT", False, None, 1),
                    ("turn_id", "TEXT", True, None, 0),
                    ("step_id", "TEXT", True, None, 0),
                    ("tool_call_id", "TEXT", True, None, 0),
                    ("tool_name", "TEXT", True, None, 0),
                    ("arguments_json", "TEXT", True, None, 0),
                    ("reason", "TEXT", True, None, 0),
                    ("state", "TEXT", True, None, 0),
                    ("created_at", "REAL", True, None, 0),
                    ("resolved_at", "REAL", False, None, 0),
                    ("resolved_by", "TEXT", True, None, 0),
                ),
                label="Conversation Approval",
            )
            require_foreign_keys(
                db,
                "conversation_approvals",
                (("conversation_turns", "turn_id", "turn_id", "NO ACTION", "CASCADE"),),
                label="Conversation Approval",
            )

    @staticmethod
    def _owned_session(db: sqlite3.Connection, scope: RuntimeScope) -> None:
        row = db.execute(
            """SELECT 1 FROM runtime_sessions
               WHERE session_handle=? AND principal_id=?""",
            (scope.session_handle, scope.principal_id),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError()

    @staticmethod
    def _owned_turn(
        db: sqlite3.Connection,
        principal_id: str,
        turn_id: str,
    ) -> sqlite3.Row:
        row = db.execute(
            """SELECT * FROM conversation_turns
               WHERE turn_id=? AND principal_id=?""",
            (turn_id, principal_id),
        ).fetchone()
        if row is None:
            raise ChatTurnNotFoundError(turn_id)
        return row

    @staticmethod
    def _attachments(payload: str) -> tuple[ArtifactAttachment, ...]:
        decoded = json.loads(payload)
        return tuple(ArtifactAttachment.model_validate(item) for item in decoded)

    def _turn(self, db: sqlite3.Connection, row: sqlite3.Row) -> ChatTurn:
        turn_id = str(row["turn_id"])
        steps = tuple(
            ChatToolStep(
                step_id=str(step["step_id"]),
                tool_call_id=str(step["tool_call_id"]),
                tool_name=str(step["tool_name"]),
                arguments=json.loads(str(step["arguments_json"])),
                state=str(step["state"]),
                result=json.loads(str(step["result_json"])),
                created_at=float(step["created_at"]),
                updated_at=float(step["updated_at"]),
            )
            for step in db.execute(
                """SELECT * FROM conversation_tool_steps
                   WHERE turn_id=? ORDER BY created_at, step_id""",
                (turn_id,),
            )
        )
        approvals = tuple(
            ChatApproval(
                approval_id=str(item["approval_id"]),
                step_id=str(item["step_id"]),
                tool_call_id=str(item["tool_call_id"]),
                tool_name=str(item["tool_name"]),
                arguments=json.loads(str(item["arguments_json"])),
                reason=str(item["reason"]),
                state=str(item["state"]),
                created_at=float(item["created_at"]),
                resolved_at=(
                    None if item["resolved_at"] is None else float(item["resolved_at"])
                ),
                resolved_by=str(item["resolved_by"]),
            )
            for item in db.execute(
                """SELECT * FROM conversation_approvals
                   WHERE turn_id=? ORDER BY created_at, approval_id""",
                (turn_id,),
            )
        )
        return ChatTurn(
            turn_id=turn_id,
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            client_request_id=str(row["client_request_id"]),
            user_input=str(row["user_input"]),
            attachments=self._attachments(str(row["attachments_json"])),
            tools_enabled=bool(row["tools_enabled"]),
            state=ChatTurnState(str(row["state"])),
            reasoning=str(row["reasoning"]),
            content=str(row["content"]),
            final_output=str(row["final_output"]),
            artifacts=tuple(
                ArtifactRef.model_validate(item)
                for item in json.loads(str(row["artifacts_json"]))
            ),
            failure_code=str(row["failure_code"]),
            cancel_requested=bool(row["cancel_requested"]),
            tool_steps=steps,
            approvals=approvals,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            finished_at=(None if row["finished_at"] is None else float(row["finished_at"])),
            revision=int(row["revision"]),
        )

    def create(
        self,
        scope: RuntimeScope,
        *,
        client_request_id: str,
        user_input: str,
        attachments: tuple[ArtifactAttachment, ...] = (),
        tools_enabled: bool = True,
    ) -> tuple[ChatTurn, bool]:
        if not user_input.strip() and not attachments:
            raise ValueError("ChatTurn requires input or an attachment")
        payload = json.dumps(
            [item.model_dump(mode="json") for item in attachments],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        now = self._clock()
        with self._connect() as db:
            self._owned_session(db, scope)
            existing = db.execute(
                """SELECT * FROM conversation_turns
                   WHERE principal_id=? AND client_request_id=?""",
                (scope.principal_id, client_request_id),
            ).fetchone()
            if existing is not None:
                turn = self._turn(db, existing)
                if (
                    turn.session_handle != scope.session_handle
                    or turn.user_input != user_input
                    or turn.attachments != attachments
                    or turn.tools_enabled != tools_enabled
                ):
                    raise ChatTurnConflictError(client_request_id)
                return turn, False
            turn_id = self._turn_id_factory().strip()
            db.execute(
                """INSERT INTO conversation_turns(
                       turn_id, principal_id, session_handle, client_request_id,
                       user_input, attachments_json, tools_enabled, state,
                       reasoning, content, final_output, artifacts_json, failure_code,
                       cancel_requested, created_at, updated_at, finished_at,
                       revision
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', '', '[]', '', 0, ?, ?, NULL, 1)""",
                (
                    turn_id,
                    scope.principal_id,
                    scope.session_handle,
                    client_request_id,
                    user_input,
                    payload,
                    int(tools_enabled),
                    ChatTurnState.RUNNING.value,
                    now,
                    now,
                ),
            )
            row = self._owned_turn(db, scope.principal_id, turn_id)
            return self._turn(db, row), True

    def get(self, principal_id: str, turn_id: str) -> ChatTurn:
        with self._connect() as db:
            return self._turn(db, self._owned_turn(db, principal_id, turn_id))

    def get_by_id(self, turn_id: str) -> ChatTurn:
        """Internal lookup used only for owner-preserving live notifications."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM conversation_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise ChatTurnNotFoundError(turn_id)
            return self._turn(db, row)

    def list_session(
        self,
        principal_id: str,
        session_handle: str,
        *,
        limit: int = 100,
    ) -> tuple[ChatTurn, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM conversation_turns
                   WHERE principal_id=? AND session_handle=?
                   ORDER BY created_at, turn_id LIMIT ?""",
                (principal_id, session_handle, limit),
            ).fetchall()
            return tuple(self._turn(db, row) for row in rows)

    def checkpoint(
        self,
        principal_id: str,
        turn_id: str,
        *,
        state: ChatTurnState | None = None,
        reasoning: str | None = None,
        content: str | None = None,
        final_output: str | None = None,
        artifacts: tuple[ArtifactRef, ...] | None = None,
        failure_code: str | None = None,
        cancel_requested: bool | None = None,
        finished: bool = False,
    ) -> ChatTurn:
        now = self._clock()
        with self._connect() as db:
            row = self._owned_turn(db, principal_id, turn_id)
            current = ChatTurnState(str(row["state"]))
            if current in TERMINAL_CHAT_TURN_STATES:
                return self._turn(db, row)
            next_state = state or current
            if finished and next_state not in TERMINAL_CHAT_TURN_STATES:
                raise ValueError("A finished ChatTurn must be terminal")
            if not finished and next_state in TERMINAL_CHAT_TURN_STATES:
                raise ValueError("A terminal ChatTurn must be finished")
            values = {
                "state": next_state.value,
                "reasoning": reasoning if reasoning is not None else str(row["reasoning"]),
                "content": content if content is not None else str(row["content"]),
                "final_output": (
                    final_output if final_output is not None else str(row["final_output"])
                ),
                "artifacts_json": (
                    json.dumps(
                        [artifact.model_dump(mode="json") for artifact in artifacts],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if artifacts is not None
                    else str(row["artifacts_json"])
                ),
                "failure_code": (
                    failure_code if failure_code is not None else str(row["failure_code"])
                ),
                "cancel_requested": (
                    int(cancel_requested)
                    if cancel_requested is not None
                    else int(row["cancel_requested"])
                ),
            }
            db.execute(
                """UPDATE conversation_turns SET
                       state=?, reasoning=?, content=?, final_output=?, artifacts_json=?,
                       failure_code=?, cancel_requested=?, updated_at=?,
                       finished_at=?, revision=revision+1
                   WHERE turn_id=? AND principal_id=?""",
                (
                    values["state"],
                    values["reasoning"],
                    values["content"],
                    values["final_output"],
                    values["artifacts_json"],
                    values["failure_code"],
                    values["cancel_requested"],
                    now,
                    now if finished else row["finished_at"],
                    turn_id,
                    principal_id,
                ),
            )
            return self._turn(db, self._owned_turn(db, principal_id, turn_id))

    def recover_interrupted(self) -> tuple[ChatTurn, ...]:
        now = self._clock()
        with self._connect() as db:
            rows = db.execute(
                """SELECT principal_id, turn_id FROM conversation_turns
                   WHERE state IN (?, ?)""",
                (
                    ChatTurnState.RUNNING.value,
                    ChatTurnState.WAITING_APPROVAL.value,
                ),
            ).fetchall()
            db.execute(
                """UPDATE conversation_turns SET state=?, failure_code=?,
                       updated_at=?, finished_at=?, revision=revision+1
                   WHERE state IN (?, ?)""",
                (
                    ChatTurnState.FAILED.value,
                    "service_restarted",
                    now,
                    now,
                    ChatTurnState.RUNNING.value,
                    ChatTurnState.WAITING_APPROVAL.value,
                ),
            )
            db.execute(
                """UPDATE conversation_approvals SET state='expired',
                       resolved_at=?, resolved_by='service_restart'
                   WHERE state='pending'""",
                (now,),
            )
            return tuple(
                self._turn(
                    db,
                    self._owned_turn(db, str(row["principal_id"]), str(row["turn_id"])),
                )
                for row in rows
            )

    def compact_expired_details(self) -> int:
        """Remove old working drafts while preserving canonical final turns."""
        cutoff = self._clock() - self._detail_retention_seconds
        terminal_states = tuple(state.value for state in TERMINAL_CHAT_TURN_STATES)
        placeholders = ",".join("?" for _ in terminal_states)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"""SELECT turn_id FROM conversation_turns
                    WHERE state IN ({placeholders})
                      AND finished_at IS NOT NULL
                      AND finished_at<=?
                      AND (reasoning<>'' OR content<>'')""",
                (*terminal_states, cutoff),
            ).fetchall()
            turn_ids = tuple(str(row["turn_id"]) for row in rows)
            if not turn_ids:
                return 0
            ids = ",".join("?" for _ in turn_ids)
            db.execute(
                f"""UPDATE conversation_turns SET
                       reasoning='', content='', revision=revision+1
                    WHERE turn_id IN ({ids})""",
                turn_ids,
            )
            db.execute(
                f"""UPDATE conversation_tool_steps SET result_json='{{}}'
                    WHERE turn_id IN ({ids})""",
                turn_ids,
            )
            return len(turn_ids)

    def begin_tool_step(
        self,
        principal_id: str,
        turn_id: str,
        *,
        step_id: str,
        call: ProposedToolCall,
        policy: ToolPolicy,
    ) -> tuple[ChatToolStep, bool]:
        del policy
        now = self._clock()
        with self._connect() as db:
            self._owned_turn(db, principal_id, turn_id)
            existing = db.execute(
                "SELECT * FROM conversation_tool_steps WHERE step_id=?",
                (step_id,),
            ).fetchone()
            if existing is None:
                db.execute(
                    """INSERT INTO conversation_tool_steps(
                           step_id, turn_id, tool_call_id, tool_name,
                           arguments_json, state, result_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'running', '{}', ?, ?)""",
                    (
                        step_id,
                        turn_id,
                        call.call_id,
                        call.name,
                        json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                        now,
                        now,
                    ),
                )
                created = True
            else:
                created = False
                if (
                    str(existing["turn_id"]) != turn_id
                    or str(existing["tool_call_id"]) != call.call_id
                    or str(existing["tool_name"]) != call.name
                    or json.loads(str(existing["arguments_json"])) != call.arguments
                ):
                    raise ChatTurnConflictError(step_id)
            row = db.execute(
                "SELECT * FROM conversation_tool_steps WHERE step_id=?",
                (step_id,),
            ).fetchone()
            assert row is not None
            return self._tool_step(row), created

    @staticmethod
    def _tool_step(row: sqlite3.Row) -> ChatToolStep:
        return ChatToolStep(
            step_id=str(row["step_id"]),
            tool_call_id=str(row["tool_call_id"]),
            tool_name=str(row["tool_name"]),
            arguments=json.loads(str(row["arguments_json"])),
            state=str(row["state"]),
            result=json.loads(str(row["result_json"])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def finish_tool_step(
        self,
        principal_id: str,
        turn_id: str,
        step_id: str,
        result: ToolStepResult,
    ) -> ChatToolStep:
        now = self._clock()
        state = "completed" if result.status == "completed" else "failed"
        with self._connect() as db:
            self._owned_turn(db, principal_id, turn_id)
            db.execute(
                """UPDATE conversation_tool_steps SET state=?, result_json=?,
                       updated_at=? WHERE step_id=? AND turn_id=?""",
                (
                    state,
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
                    now,
                    step_id,
                    turn_id,
                ),
            )
            row = db.execute(
                "SELECT * FROM conversation_tool_steps WHERE step_id=? AND turn_id=?",
                (step_id, turn_id),
            ).fetchone()
            if row is None:
                raise ChatTurnNotFoundError(step_id)
            return self._tool_step(row)

    def request_approval(
        self,
        principal_id: str,
        turn_id: str,
        *,
        step_id: str,
        call: ProposedToolCall,
        reason: str,
    ) -> tuple[ChatApproval, bool]:
        now = self._clock()
        with self._connect() as db:
            self._owned_turn(db, principal_id, turn_id)
            row = db.execute(
                """SELECT * FROM conversation_approvals
                   WHERE turn_id=? AND step_id=?""",
                (turn_id, step_id),
            ).fetchone()
            created = row is None
            if row is None:
                approval_id = self._approval_id_factory().strip()
                db.execute(
                    """INSERT INTO conversation_approvals(
                           approval_id, turn_id, step_id, tool_call_id, tool_name,
                           arguments_json, reason, state, created_at, resolved_at,
                           resolved_by
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, '')""",
                    (
                        approval_id,
                        turn_id,
                        step_id,
                        call.call_id,
                        call.name,
                        json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                        reason,
                        now,
                    ),
                )
                db.execute(
                    """UPDATE conversation_turns SET state=?, updated_at=?,
                           revision=revision+1 WHERE turn_id=?""",
                    (ChatTurnState.WAITING_APPROVAL.value, now, turn_id),
                )
                row = db.execute(
                    "SELECT * FROM conversation_approvals WHERE approval_id=?",
                    (approval_id,),
                ).fetchone()
            assert row is not None
            return self._approval(row), created

    @staticmethod
    def _approval(row: sqlite3.Row) -> ChatApproval:
        return ChatApproval(
            approval_id=str(row["approval_id"]),
            step_id=str(row["step_id"]),
            tool_call_id=str(row["tool_call_id"]),
            tool_name=str(row["tool_name"]),
            arguments=json.loads(str(row["arguments_json"])),
            reason=str(row["reason"]),
            state=str(row["state"]),
            created_at=float(row["created_at"]),
            resolved_at=(None if row["resolved_at"] is None else float(row["resolved_at"])),
            resolved_by=str(row["resolved_by"]),
        )

    def resolve_approval(
        self,
        principal_id: str,
        approval_id: str,
        *,
        approved: bool,
        resolved_by: str,
    ) -> tuple[ChatApproval, bool, str]:
        now = self._clock()
        with self._connect() as db:
            row = db.execute(
                """SELECT a.* FROM conversation_approvals a
                   JOIN conversation_turns t ON t.turn_id=a.turn_id
                   WHERE a.approval_id=? AND t.principal_id=?""",
                (approval_id, principal_id),
            ).fetchone()
            if row is None:
                raise ChatTurnNotFoundError(approval_id)
            changed = str(row["state"]) == "pending"
            if changed:
                state = "approved" if approved else "rejected"
                db.execute(
                    """UPDATE conversation_approvals SET state=?, resolved_at=?,
                           resolved_by=? WHERE approval_id=?""",
                    (state, now, resolved_by, approval_id),
                )
                db.execute(
                    """UPDATE conversation_turns SET state=?, updated_at=?,
                           revision=revision+1 WHERE turn_id=?""",
                    (ChatTurnState.RUNNING.value, now, str(row["turn_id"])),
                )
            resolved = db.execute(
                "SELECT * FROM conversation_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            assert resolved is not None
            return self._approval(resolved), changed, str(row["turn_id"])
