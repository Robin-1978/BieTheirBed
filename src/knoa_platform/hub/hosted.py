"""Single-node Hosted Hub control plane with isolated Workspace compositions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Mount, Route

from knoa_platform.console_ui import hub_console_html
from knoa_platform.hub.app import HubApplication
from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService
from knoa_platform.host_lifecycle_client import HostLifecycleClient
from knoa_platform.mobile_releases import (
    AndroidRelease,
    AndroidReleaseRepository,
    android_release_payload,
)
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal

DEPLOYMENT_MODE = "hosted_single_node"
_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_PASSWORD_BYTES = 32
_MAX_REMOTE_APK_BYTES = 100 * 1024 * 1024
_MAX_REMOTE_RELEASE_NOTES_BYTES = 4_000


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AccountEnrollmentGrantRequest(_Request):
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class HostedAccountRequest(_Request):
    grant_id: str = Field(min_length=1, max_length=128)
    grant_secret: str = Field(min_length=32, max_length=256)
    login_identity: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=1024)


class HostedSessionRequest(_Request):
    login_identity: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)


class HostedWorkspaceRequest(_Request):
    display_name: str = Field(min_length=1, max_length=128)
    kind: Literal["shared"] = "shared"


class HostedPasswordChangeRequest(_Request):
    current_password: str = Field(min_length=12, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class HostedPasswordResetGrantRequest(_Request):
    login_identity: str = Field(min_length=3, max_length=254)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class HostedPasswordResetRequest(_Request):
    grant_id: str = Field(min_length=1, max_length=128)
    grant_secret: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=1024)


class HostedWorkspaceMemberRequest(_Request):
    login_identity: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "member"] = "member"


class HostedWorkspaceOwnerTransferRequest(_Request):
    account_id: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class AccountEnrollmentGrant:
    grant_id: str
    secret: str
    expires_at: float


@dataclass(frozen=True)
class PasswordResetGrant:
    grant_id: str
    secret: str
    expires_at: float


class _WindowLimiter:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: float = 60.0) -> bool:
        now = float(self._clock())
        bucket = self._requests[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _normalize_login(value: str) -> str:
    normalized = value.strip().casefold()
    if not re.fullmatch(r"[^\s]{3,254}", normalized):
        raise ValueError("Hosted login identity is invalid")
    return normalized


def _display_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Display name is required")
    return normalized


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        dklen=_PASSWORD_BYTES,
    )


class HostedControlRepository:
    """Account, session, Workspace and membership authority for one Hosted Hub."""

    def __init__(self, path: str | Path, *, hub_id: str, clock=time.time) -> None:
        self.path = Path(path).expanduser().resolve()
        self.hub_id = hub_id
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        initialize_wal(self.path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS hosted_accounts(
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active', 'disabled')),
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosted_login_identities(
                    identity_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES hosted_accounts(account_id),
                    login_identity TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK(state IN ('active', 'disabled')),
                    verified_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosted_password_credentials(
                    account_id TEXT PRIMARY KEY REFERENCES hosted_accounts(account_id),
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosted_workspaces(
                    workspace_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('personal', 'shared')),
                    state TEXT NOT NULL CHECK(state IN ('active', 'archived')),
                    created_by_account_id TEXT NOT NULL REFERENCES hosted_accounts(account_id),
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosted_workspace_memberships(
                    workspace_id TEXT NOT NULL REFERENCES hosted_workspaces(workspace_id),
                    account_id TEXT NOT NULL REFERENCES hosted_accounts(account_id),
                    role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'member')),
                    state TEXT NOT NULL CHECK(state IN ('active', 'revoked')),
                    created_at REAL NOT NULL,
                    PRIMARY KEY(workspace_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS hosted_account_sessions(
                    session_digest TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL REFERENCES hosted_accounts(account_id),
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS hosted_account_enrollment_grants(
                    grant_id TEXT PRIMARY KEY,
                    secret_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS hosted_password_reset_grants(
                    grant_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES hosted_accounts(account_id),
                    secret_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_hosted_memberships_account
                    ON hosted_workspace_memberships(account_id, state);
                CREATE INDEX IF NOT EXISTS idx_hosted_sessions_account
                    ON hosted_account_sessions(account_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_hosted_password_resets_account
                    ON hosted_password_reset_grants(account_id, expires_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, foreign_keys=True)

    def create_account_enrollment_grant(
        self,
        ttl_seconds: int = 900,
    ) -> AccountEnrollmentGrant:
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError(
                "Account enrollment TTL must be between 60 and 3600 seconds"
            )
        now = self._clock()
        grant = AccountEnrollmentGrant(
            grant_id=f"haeg_{secrets.token_urlsafe(18)}",
            secret=f"has_{secrets.token_urlsafe(36)}",
            expires_at=now + ttl_seconds,
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO hosted_account_enrollment_grants VALUES (?, ?, ?, ?, NULL)",
                (
                    grant.grant_id,
                    _token_digest(grant.secret),
                    now,
                    grant.expires_at,
                ),
            )
        return grant

    def create_account(
        self,
        *,
        grant_id: str,
        grant_secret: str,
        login_identity: str,
        display_name: str,
        password: str,
        session_ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> dict[str, Any]:
        login = _normalize_login(login_identity)
        name = _display_name(display_name)
        if len(password) < 12:
            raise ValueError("Hosted account password is too short")
        now = self._clock()
        account_id = f"acct_{secrets.token_urlsafe(18)}"
        identity_id = f"idn_{secrets.token_urlsafe(18)}"
        workspace_id = f"ws_{secrets.token_urlsafe(18)}"
        salt = secrets.token_bytes(16)
        password_digest = _password_hash(password, salt)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            grant = db.execute(
                "SELECT * FROM hosted_account_enrollment_grants WHERE grant_id=?",
                (grant_id,),
            ).fetchone()
            if (
                grant is None
                or grant["consumed_at"] is not None
                or float(grant["expires_at"]) <= now
                or not secrets.compare_digest(
                    str(grant["secret_digest"]),
                    _token_digest(grant_secret),
                )
            ):
                raise PermissionError("Hosted account enrollment rejected")
            try:
                db.execute(
                    "INSERT INTO hosted_accounts VALUES (?, ?, 'active', ?)",
                    (account_id, name, now),
                )
                db.execute(
                    "INSERT INTO hosted_login_identities VALUES (?, ?, ?, 'active', ?, ?)",
                    (identity_id, account_id, login, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise FileExistsError("Hosted account already exists") from exc
            db.execute(
                "INSERT INTO hosted_password_credentials VALUES (?, ?, ?, ?)",
                (account_id, salt, password_digest, now),
            )
            db.execute(
                "INSERT INTO hosted_workspaces VALUES (?, ?, 'personal', 'active', ?, ?)",
                (workspace_id, f"{name} 的 Personal Workspace", account_id, now),
            )
            db.execute(
                "INSERT INTO hosted_workspace_memberships VALUES (?, ?, 'owner', 'active', ?)",
                (workspace_id, account_id, now),
            )
            updated = db.execute(
                """UPDATE hosted_account_enrollment_grants SET consumed_at=?
                   WHERE grant_id=? AND consumed_at IS NULL""",
                (now, grant_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("Hosted account enrollment rejected")
            session = self._issue_session(db, account_id, session_ttl_seconds, now)
        return {
            "account_id": account_id,
            "login_identity": login,
            "display_name": name,
            "access_token": session["access_token"],
            "expires_at": session["expires_at"],
            "default_workspace_id": workspace_id,
            "workspaces": self.list_workspaces(account_id),
        }

    def login(
        self,
        login_identity: str,
        password: str,
        *,
        session_ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> dict[str, Any]:
        login = _normalize_login(login_identity)
        with self._connect() as db:
            row = db.execute(
                """SELECT a.account_id, a.display_name, c.salt, c.password_hash
                   FROM hosted_login_identities i
                   JOIN hosted_accounts a ON a.account_id=i.account_id
                   JOIN hosted_password_credentials c ON c.account_id=a.account_id
                   WHERE i.login_identity=? AND i.state='active' AND a.state='active'""",
                (login,),
            ).fetchone()
        if row is None or not secrets.compare_digest(
            bytes(row["password_hash"]),
            _password_hash(password, bytes(row["salt"])),
        ):
            raise PermissionError("Hosted account login rejected")
        now = self._clock()
        with self._connect() as db:
            session = self._issue_session(
                db,
                str(row["account_id"]),
                session_ttl_seconds,
                now,
            )
        workspaces = self.list_workspaces(str(row["account_id"]))
        if not workspaces:
            raise RuntimeError("Hosted account has no active Workspace")
        return {
            "account_id": row["account_id"],
            "login_identity": login,
            "display_name": row["display_name"],
            "access_token": session["access_token"],
            "expires_at": session["expires_at"],
            "default_workspace_id": workspaces[0]["workspace_id"],
            "workspaces": workspaces,
        }

    def authenticate(self, token: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT a.account_id, a.display_name, i.login_identity,
                          s.session_id, s.expires_at
                   FROM hosted_account_sessions s
                   JOIN hosted_accounts a ON a.account_id=s.account_id
                   JOIN hosted_login_identities i ON i.account_id=a.account_id
                   WHERE s.session_digest=? AND s.revoked_at IS NULL
                     AND s.expires_at>? AND a.state='active' AND i.state='active'""",
                (_token_digest(token), self._clock()),
            ).fetchone()
        if row is None:
            raise PermissionError("Hosted account authentication rejected")
        return dict(row)

    def revoke_session(self, token: str) -> None:
        with self._connect() as db:
            updated = db.execute(
                """UPDATE hosted_account_sessions SET revoked_at=?
                   WHERE session_digest=? AND revoked_at IS NULL""",
                (self._clock(), _token_digest(token)),
            )
        if updated.rowcount != 1:
            raise PermissionError("Hosted account session rejected")

    def change_password(
        self,
        account_id: str,
        current_password: str,
        new_password: str,
        *,
        current_token: str,
    ) -> None:
        if len(new_password) < 12:
            raise ValueError("Hosted account password is too short")
        with self._connect() as db:
            credential = db.execute(
                "SELECT salt, password_hash FROM hosted_password_credentials WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if credential is None or not secrets.compare_digest(
            bytes(credential["password_hash"]),
            _password_hash(current_password, bytes(credential["salt"])),
        ):
            raise PermissionError("Hosted account password rejected")
        salt = secrets.token_bytes(16)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE hosted_password_credentials
                   SET salt=?, password_hash=?, updated_at=? WHERE account_id=?""",
                (salt, _password_hash(new_password, salt), self._clock(), account_id),
            )
            db.execute(
                """UPDATE hosted_account_sessions SET revoked_at=?
                   WHERE account_id=? AND session_digest<>? AND revoked_at IS NULL""",
                (self._clock(), account_id, _token_digest(current_token)),
            )

    def create_password_reset_grant(
        self,
        login_identity: str,
        ttl_seconds: int = 900,
    ) -> PasswordResetGrant:
        if not 60 <= ttl_seconds <= 3600:
            raise ValueError("Password reset TTL must be between 60 and 3600 seconds")
        login = _normalize_login(login_identity)
        now = self._clock()
        with self._connect() as db:
            account = db.execute(
                """SELECT a.account_id FROM hosted_login_identities i
                   JOIN hosted_accounts a ON a.account_id=i.account_id
                   WHERE i.login_identity=? AND i.state='active' AND a.state='active'""",
                (login,),
            ).fetchone()
            if account is None:
                raise LookupError("Hosted account not found")
            grant = PasswordResetGrant(
                grant_id=f"hprg_{secrets.token_urlsafe(18)}",
                secret=f"hprs_{secrets.token_urlsafe(36)}",
                expires_at=now + ttl_seconds,
            )
            db.execute(
                "INSERT INTO hosted_password_reset_grants VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    grant.grant_id,
                    str(account["account_id"]),
                    _token_digest(grant.secret),
                    now,
                    grant.expires_at,
                ),
            )
        return grant

    def reset_password(
        self,
        *,
        grant_id: str,
        grant_secret: str,
        new_password: str,
        session_ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> dict[str, Any]:
        if len(new_password) < 12:
            raise ValueError("Hosted account password is too short")
        now = self._clock()
        salt = secrets.token_bytes(16)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            grant = db.execute(
                "SELECT * FROM hosted_password_reset_grants WHERE grant_id=?",
                (grant_id,),
            ).fetchone()
            if (
                grant is None
                or grant["consumed_at"] is not None
                or float(grant["expires_at"]) <= now
                or not secrets.compare_digest(
                    str(grant["secret_digest"]),
                    _token_digest(grant_secret),
                )
            ):
                raise PermissionError("Hosted password reset rejected")
            account_id = str(grant["account_id"])
            db.execute(
                """UPDATE hosted_password_credentials
                   SET salt=?, password_hash=?, updated_at=? WHERE account_id=?""",
                (salt, _password_hash(new_password, salt), now, account_id),
            )
            db.execute(
                """UPDATE hosted_account_sessions SET revoked_at=?
                   WHERE account_id=? AND revoked_at IS NULL""",
                (now, account_id),
            )
            updated = db.execute(
                """UPDATE hosted_password_reset_grants SET consumed_at=?
                   WHERE grant_id=? AND consumed_at IS NULL""",
                (now, grant_id),
            )
            if updated.rowcount != 1:
                raise PermissionError("Hosted password reset rejected")
            session = self._issue_session(db, account_id, session_ttl_seconds, now)
            account = db.execute(
                """SELECT a.account_id, a.display_name, i.login_identity
                   FROM hosted_accounts a
                   JOIN hosted_login_identities i ON i.account_id=a.account_id
                   WHERE a.account_id=? AND a.state='active' AND i.state='active'""",
                (account_id,),
            ).fetchone()
        if account is None:
            raise PermissionError("Hosted password reset rejected")
        workspaces = self.list_workspaces(account_id)
        if not workspaces:
            raise RuntimeError("Hosted account has no active Workspace")
        return {
            **dict(account),
            "access_token": session["access_token"],
            "expires_at": session["expires_at"],
            "default_workspace_id": workspaces[0]["workspace_id"],
            "workspaces": workspaces,
        }

    def list_workspaces(self, account_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT w.workspace_id, w.display_name, w.kind, m.role, w.created_at
                   FROM hosted_workspace_memberships m
                   JOIN hosted_workspaces w ON w.workspace_id=m.workspace_id
                   WHERE m.account_id=? AND m.state='active' AND w.state='active'
                   ORDER BY CASE w.kind WHEN 'personal' THEN 0 ELSE 1 END,
                            w.created_at, w.workspace_id""",
                (account_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_workspace(
        self,
        account_id: str,
        display_name: str,
        *,
        kind: str = "shared",
    ) -> dict[str, Any]:
        name = _display_name(display_name)
        if kind != "shared":
            raise ValueError("Only shared user-created Workspaces are supported")
        now = self._clock()
        workspace_id = f"ws_{secrets.token_urlsafe(18)}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute(
                "SELECT account_id FROM hosted_accounts WHERE account_id=? AND state='active'",
                (account_id,),
            ).fetchone()
            if account is None:
                raise PermissionError("Hosted account rejected")
            db.execute(
                "INSERT INTO hosted_workspaces VALUES (?, ?, ?, 'active', ?, ?)",
                (workspace_id, name, kind, account_id, now),
            )
            db.execute(
                "INSERT INTO hosted_workspace_memberships VALUES (?, ?, 'owner', 'active', ?)",
                (workspace_id, account_id, now),
            )
        return self.workspace(workspace_id)

    def list_workspace_members(
        self,
        requester_account_id: str,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        self._require_workspace_role(
            requester_account_id,
            workspace_id,
            {"owner", "admin"},
        )
        with self._connect() as db:
            rows = db.execute(
                """SELECT m.account_id, a.display_name, i.login_identity, m.role,
                          m.created_at
                   FROM hosted_workspace_memberships m
                   JOIN hosted_accounts a ON a.account_id=m.account_id
                   JOIN hosted_login_identities i ON i.account_id=m.account_id
                   WHERE m.workspace_id=? AND m.state='active'
                     AND a.state='active' AND i.state='active'
                   ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                            m.created_at, m.account_id""",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_workspace_member(
        self,
        requester_account_id: str,
        workspace_id: str,
        login_identity: str,
        role: str,
    ) -> dict[str, Any]:
        self._require_workspace_role(requester_account_id, workspace_id, {"owner"})
        if role not in {"admin", "member"}:
            raise ValueError("Hosted Workspace membership role is invalid")
        login = _normalize_login(login_identity)
        now = self._clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            workspace = db.execute(
                "SELECT kind FROM hosted_workspaces WHERE workspace_id=? AND state='active'",
                (workspace_id,),
            ).fetchone()
            if workspace is None or workspace["kind"] != "shared":
                raise ValueError("Only shared Workspaces accept additional members")
            account = db.execute(
                """SELECT a.account_id FROM hosted_login_identities i
                   JOIN hosted_accounts a ON a.account_id=i.account_id
                   WHERE i.login_identity=? AND i.state='active' AND a.state='active'""",
                (login,),
            ).fetchone()
            if account is None:
                raise LookupError("Hosted account not found")
            account_id = str(account["account_id"])
            existing = db.execute(
                """SELECT role FROM hosted_workspace_memberships
                   WHERE workspace_id=? AND account_id=?""",
                (workspace_id, account_id),
            ).fetchone()
            if existing is not None and existing["role"] == "owner":
                raise ValueError("Workspace owner role cannot be replaced")
            db.execute(
                """INSERT INTO hosted_workspace_memberships
                   VALUES (?, ?, ?, 'active', ?)
                   ON CONFLICT(workspace_id, account_id) DO UPDATE SET
                     role=excluded.role, state='active'""",
                (workspace_id, account_id, role, now),
            )
        return next(
            item
            for item in self.list_workspace_members(requester_account_id, workspace_id)
            if item["account_id"] == account_id
        )

    def remove_workspace_member(
        self,
        requester_account_id: str,
        workspace_id: str,
        account_id: str,
    ) -> None:
        self._require_workspace_role(requester_account_id, workspace_id, {"owner"})
        with self._connect() as db:
            membership = db.execute(
                """SELECT role FROM hosted_workspace_memberships
                   WHERE workspace_id=? AND account_id=? AND state='active'""",
                (workspace_id, account_id),
            ).fetchone()
            if membership is None:
                raise LookupError("Hosted Workspace membership not found")
            if membership["role"] == "owner":
                raise ValueError("Workspace owner cannot be removed")
            db.execute(
                """UPDATE hosted_workspace_memberships SET state='revoked'
                   WHERE workspace_id=? AND account_id=?""",
                (workspace_id, account_id),
            )

    def transfer_workspace_ownership(
        self,
        requester_account_id: str,
        workspace_id: str,
        target_account_id: str,
    ) -> None:
        self._require_workspace_role(requester_account_id, workspace_id, {"owner"})
        if requester_account_id == target_account_id:
            return
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            workspace = db.execute(
                "SELECT kind FROM hosted_workspaces WHERE workspace_id=? AND state='active'",
                (workspace_id,),
            ).fetchone()
            if workspace is None or workspace["kind"] != "shared":
                raise ValueError("Personal Workspace ownership cannot be transferred")
            target = db.execute(
                """SELECT role FROM hosted_workspace_memberships
                   WHERE workspace_id=? AND account_id=? AND state='active'""",
                (workspace_id, target_account_id),
            ).fetchone()
            if target is None:
                raise LookupError("Target Workspace member not found")
            db.execute(
                """UPDATE hosted_workspace_memberships SET role='admin'
                   WHERE workspace_id=? AND account_id=? AND role='owner'""",
                (workspace_id, requester_account_id),
            )
            promoted = db.execute(
                """UPDATE hosted_workspace_memberships SET role='owner'
                   WHERE workspace_id=? AND account_id=? AND state='active'""",
                (workspace_id, target_account_id),
            )
            if promoted.rowcount != 1:
                raise LookupError("Target Workspace member not found")

    def authenticate_workspace(
        self,
        token: str,
        workspace_id: str,
        *,
        roles: set[str] | None = None,
    ) -> str:
        allowed_roles = roles or {"owner", "admin", "member"}
        with self._connect() as db:
            row = db.execute(
                """SELECT a.account_id, m.role
                   FROM hosted_account_sessions s
                   JOIN hosted_accounts a ON a.account_id=s.account_id
                   JOIN hosted_workspace_memberships m ON m.account_id=a.account_id
                   JOIN hosted_workspaces w ON w.workspace_id=m.workspace_id
                   WHERE s.session_digest=? AND s.revoked_at IS NULL
                     AND s.expires_at>? AND a.state='active'
                     AND m.workspace_id=? AND m.state='active' AND w.state='active'""",
                (_token_digest(token), self._clock(), workspace_id),
            ).fetchone()
        if row is None:
            raise PermissionError("Hosted Workspace authentication rejected")
        if str(row["role"]) not in allowed_roles:
            raise PermissionError("Hosted Workspace authorization rejected")
        return str(row["account_id"])

    def _require_workspace_role(
        self,
        account_id: str,
        workspace_id: str,
        roles: set[str],
    ) -> str:
        with self._connect() as db:
            row = db.execute(
                """SELECT m.role FROM hosted_workspace_memberships m
                   JOIN hosted_workspaces w ON w.workspace_id=m.workspace_id
                   WHERE m.account_id=? AND m.workspace_id=?
                     AND m.state='active' AND w.state='active'""",
                (account_id, workspace_id),
            ).fetchone()
        if row is None or str(row["role"]) not in roles:
            raise PermissionError("Hosted Workspace authorization rejected")
        return str(row["role"])

    def workspace(self, workspace_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT w.*, m.account_id AS owner_account_id
                   FROM hosted_workspaces w
                   JOIN hosted_workspace_memberships m ON m.workspace_id=w.workspace_id
                   WHERE w.workspace_id=? AND w.state='active'
                     AND m.role='owner' AND m.state='active'
                   ORDER BY m.created_at LIMIT 1""",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Hosted Workspace not found")
        return dict(row)

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            accounts = db.execute(
                "SELECT COUNT(*) AS count FROM hosted_accounts WHERE state='active'"
            ).fetchone()
            workspaces = db.execute(
                "SELECT COUNT(*) AS count FROM hosted_workspaces WHERE state='active'"
            ).fetchone()
        return {
            "account_count": int(accounts["count"]),
            "workspace_count": int(workspaces["count"]),
        }

    @staticmethod
    def _issue_session(
        db: sqlite3.Connection,
        account_id: str,
        ttl_seconds: int,
        now: float,
    ) -> dict[str, Any]:
        access_token = f"khs_{secrets.token_urlsafe(36)}"
        expires_at = now + ttl_seconds
        db.execute(
            "INSERT INTO hosted_account_sessions VALUES (?, ?, ?, ?, ?, NULL)",
            (
                _token_digest(access_token),
                f"hsn_{secrets.token_urlsafe(18)}",
                account_id,
                now,
                expires_at,
            ),
        )
        return {"access_token": access_token, "expires_at": expires_at}


class HostedTenantDispatcher:
    """Route one URL-scoped Workspace to an isolated Hub composition."""

    def __init__(
        self,
        root: Path,
        *,
        hub_id: str,
        control: HostedControlRepository,
    ) -> None:
        self._root = root
        self._hub_id = hub_id
        self._control = control
        self._applications: dict[str, Starlette] = {}

    def application(self, workspace_id: str) -> Starlette:
        existing = self._applications.get(workspace_id)
        if existing is not None:
            return existing
        workspace = self._control.workspace(workspace_id)
        tenant_root = self._root / "tenants" / workspace_id
        repository = HubRepository(tenant_root / "hub.db", hub_id=workspace_id)
        service = HubService(
            repository,
            self._root / "hub-signing.key",
            owner_subject_id=str(workspace["owner_account_id"]),
            owner_authenticator=lambda token: self._control.authenticate_workspace(
                token,
                workspace_id,
                roles={"owner", "admin"},
            ),
            member_authenticator=lambda token: self._control.authenticate_workspace(
                token,
                workspace_id,
                roles={"owner", "admin", "member"},
            ),
            hub_id=self._hub_id,
        )
        application = HubApplication(service, deployment_mode=DEPLOYMENT_MODE).app
        self._applications[workspace_id] = application
        return application

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path", ""))
        relative = path.lstrip("/")
        if relative.startswith("workspaces/"):
            relative = relative.removeprefix("workspaces/")
        workspace_id, separator, remainder = relative.partition("/")
        if not separator or not re.fullmatch(r"ws_[A-Za-z0-9_-]{12,96}", workspace_id):
            await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send
            )
            return
        try:
            application = self.application(workspace_id)
        except LookupError:
            await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send
            )
            return
        tenant_scope = dict(scope)
        tenant_scope["path"] = f"/{remainder}"
        tenant_scope["raw_path"] = tenant_scope["path"].encode()
        tenant_scope["root_path"] = (
            f"{scope.get('root_path', '')}/{workspace_id}".rstrip("/")
        )
        await application(tenant_scope, receive, send)


class HostedHubApplication:
    """Single-node Hosted Hub root plus isolated Workspace applications."""

    def __init__(
        self,
        root: str | Path,
        *,
        hub_id: str,
        bootstrap_token: str,
        release_publish_token: str = "",
        public_url: str = "http://127.0.0.1:9529",
    ) -> None:
        if len(bootstrap_token) < 32:
            raise ValueError(
                "Hosted bootstrap token must contain at least 32 characters"
            )
        if release_publish_token and len(release_publish_token) < 32:
            raise ValueError(
                "Hosted release publish token must contain at least 32 characters"
            )
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.hub_id = hub_id
        self.public_url = public_url.strip().rstrip("/")
        if not re.fullmatch(r"https?://[^/]+(?::[0-9]+)?", self.public_url):
            raise ValueError("Hosted Hub public URL must be an HTTP(S) origin")
        self._bootstrap_digest = hashlib.sha256(bootstrap_token.encode()).digest()
        self._release_publish_digest = (
            hashlib.sha256(release_publish_token.encode()).digest()
            if release_publish_token
            else None
        )
        self._limiter = _WindowLimiter()
        self._console_csrf_token = secrets.token_urlsafe(32)
        self._host_lifecycle = HostLifecycleClient.from_environment()
        self.control = HostedControlRepository(
            self.root / "control.db",
            hub_id=hub_id,
        )
        self.mobile_releases = AndroidReleaseRepository(
            self.root / "mobile-releases" / "android"
        )
        self.tenants = HostedTenantDispatcher(
            self.root,
            hub_id=hub_id,
            control=self.control,
        )
        self.app = Starlette(
            routes=[
                Route("/health", self.health, methods=["GET"]),
                Route(
                    "/v1/hosted/account-enrollment-grants",
                    self.account_enrollment_grants,
                    methods=["POST"],
                ),
                Route("/v1/hosted/accounts", self.create_account, methods=["POST"]),
                Route("/v1/hosted/sessions", self.create_session, methods=["POST"]),
                Route("/v1/hosted/session", self.revoke_session, methods=["DELETE"]),
                Route("/v1/hosted/account", self.account, methods=["GET"]),
                Route(
                    "/v1/mobile/releases/android/latest",
                    self.latest_android_release,
                    methods=["GET"],
                ),
                Route(
                    "/v1/admin/mobile/releases/android",
                    self.publish_android_release,
                    methods=["PUT"],
                ),
                Route(
                    "/releases/android/{version_code:str}/{sha256:str}/knoa.apk",
                    self.download_android_release,
                    methods=["GET"],
                ),
                Route(
                    "/downloads/android/latest.apk",
                    self.download_latest_android_release,
                    methods=["GET"],
                ),
                Route(
                    "/v1/hosted/account/password",
                    self.change_password,
                    methods=["PATCH"],
                ),
                Route(
                    "/v1/hosted/password-reset-grants",
                    self.password_reset_grants,
                    methods=["POST"],
                ),
                Route(
                    "/v1/hosted/password-reset",
                    self.reset_password,
                    methods=["POST"],
                ),
                Route(
                    "/v1/hosted/workspaces",
                    self.workspaces,
                    methods=["GET", "POST"],
                ),
                Route(
                    "/v1/hosted/workspaces/{workspace_id:str}/members",
                    self.workspace_members,
                    methods=["GET", "POST"],
                ),
                Route(
                    "/v1/hosted/workspaces/{workspace_id:str}/members/{account_id:str}",
                    self.workspace_member,
                    methods=["DELETE"],
                ),
                Route(
                    "/v1/hosted/workspaces/{workspace_id:str}/owner-transfer",
                    self.workspace_owner_transfer,
                    methods=["POST"],
                ),
                Mount("/workspaces", app=self.tenants),
            ]
        )
        self.console_app = Starlette(
            routes=[
                Route("/console", self.console, methods=["GET"]),
                Route(
                    "/v1/console/lifecycle",
                    self.console_lifecycle,
                    methods=["GET"],
                ),
                Route(
                    "/v1/console/lifecycle/actions",
                    self.console_lifecycle_action,
                    methods=["POST"],
                ),
                Route(
                    "/v1/console/lifecycle/bundles/{name:str}",
                    self.console_lifecycle_bundle,
                    methods=["PUT"],
                ),
                Mount("/", app=self.app),
            ]
        )

    async def console(self, _request: Request) -> HTMLResponse:
        return HTMLResponse(
            hub_console_html(self._console_csrf_token, self.public_url),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def _console_authorized(self, request: Request) -> bool:
        if request.client is None:
            return False
        try:
            local = ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            return False
        return local and secrets.compare_digest(
            request.headers.get("X-Knoa-Console", ""),
            self._console_csrf_token,
        )

    async def console_lifecycle(self, request: Request) -> JSONResponse:
        if not self._console_authorized(request):
            return JSONResponse({"error": "console_csrf_rejected"}, status_code=403)
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        try:
            body = await asyncio.to_thread(self._host_lifecycle.status)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def console_lifecycle_action(self, request: Request) -> JSONResponse:
        if not self._console_authorized(request):
            return JSONResponse({"error": "console_csrf_rejected"}, status_code=403)
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        raw = await request.body()
        if len(raw) > 16 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            payload = json.loads(raw)
            body = await asyncio.to_thread(self._host_lifecycle.action, payload)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_action"}, status_code=400)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=503)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    async def console_lifecycle_bundle(self, request: Request) -> JSONResponse:
        if not self._console_authorized(request):
            return JSONResponse({"error": "console_csrf_rejected"}, status_code=403)
        if self._host_lifecycle is None:
            return JSONResponse({"error": "lifecycle_not_installed"}, status_code=503)
        name = request.path_params["name"]
        if not name.endswith(".zip"):
            return JSONResponse({"error": "invalid_bundle_name"}, status_code=400)
        try:
            destination = self._host_lifecycle.bundle_path(name)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp"
        )
        size = 0
        try:
            with temporary.open("xb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024 * 1024:
                        raise OverflowError
                    stream.write(chunk)
            if size == 0:
                raise ValueError("empty_bundle")
            os.replace(temporary, destination)
        except OverflowError:
            temporary.unlink(missing_ok=True)
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            return JSONResponse({"error": "bundle_upload_failed"}, status_code=400)
        return JSONResponse({"bundle_name": name, "size_bytes": size}, status_code=201)

    async def health(self, _request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "hub_id": self.hub_id,
                "deployment_mode": DEPLOYMENT_MODE,
                **self.control.counts(),
            }
        )

    async def account_enrollment_grants(self, request: Request) -> JSONResponse:
        if not self._allow(request, "account-grant", limit=10):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        if not self._bootstrap(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        parsed = await self._parse(request, AccountEnrollmentGrantRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        grant = self.control.create_account_enrollment_grant(parsed.ttl_seconds)
        return JSONResponse(grant.__dict__, status_code=201)

    async def create_account(self, request: Request) -> JSONResponse:
        if not self._allow(request, "account-create", limit=20):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        parsed = await self._parse(request, HostedAccountRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            account = self.control.create_account(**parsed.model_dump(mode="json"))
        except PermissionError:
            return JSONResponse({"error": "enrollment_rejected"}, status_code=401)
        except FileExistsError:
            return JSONResponse({"error": "account_exists"}, status_code=409)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        self.tenants.application(str(account["default_workspace_id"]))
        return JSONResponse(self._account_payload(account), status_code=201)

    async def create_session(self, request: Request) -> JSONResponse:
        parsed = await self._parse(request, HostedSessionRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        login_key = hashlib.sha256(
            parsed.login_identity.casefold().encode()
        ).hexdigest()
        if not self._allow(request, f"login:{login_key}", limit=10):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        try:
            account = self.control.login(parsed.login_identity, parsed.password)
        except PermissionError:
            return JSONResponse({"error": "login_rejected"}, status_code=401)
        return JSONResponse(self._account_payload(account), status_code=201)

    async def revoke_session(self, request: Request) -> JSONResponse:
        token = self._bearer(request)
        try:
            self.control.revoke_session(token)
        except PermissionError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"revoked": True})

    async def account(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        workspaces = self.control.list_workspaces(str(authenticated["account_id"]))
        return JSONResponse(
            {
                "account_id": authenticated["account_id"],
                "login_identity": authenticated["login_identity"],
                "display_name": authenticated["display_name"],
                "expires_at": authenticated["expires_at"],
                "hub_id": self.hub_id,
                "identity_issuer_id": self.hub_id,
                "deployment_mode": DEPLOYMENT_MODE,
                "workspaces": self._workspace_payloads(workspaces),
            }
        )

    async def latest_android_release(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            release = self.mobile_releases.latest()
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        assert release is not None
        return JSONResponse(
            android_release_payload(
                release,
                channel="hosted",
                download_path=(
                    f"/releases/android/{release.version_code}/"
                    f"{release.sha256}/knoa.apk"
                ),
            )
        )

    async def publish_android_release(self, request: Request) -> JSONResponse:
        if self._release_publish_digest is None:
            return JSONResponse({"error": "publisher_not_configured"}, status_code=503)
        if not self._allow(request, "mobile-publish", limit=5):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        if not self._release_publisher(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.headers.get("Content-Type", "").partition(";")[0].strip() != (
            "application/vnd.android.package-archive"
        ):
            return JSONResponse({"error": "invalid_content_type"}, status_code=415)
        declared_length = request.headers.get("Content-Length", "").strip()
        if not declared_length.isascii() or not declared_length.isdecimal():
            return JSONResponse({"error": "length_required"}, status_code=411)
        content_length = int(declared_length)
        if content_length < 1 or content_length > _MAX_REMOTE_APK_BYTES:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        declared_sha256 = request.headers.get("X-Knoa-Apk-SHA256", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", declared_sha256):
            return JSONResponse({"error": "invalid_release_metadata"}, status_code=400)
        try:
            version_name = self._release_version_name(request)
            version_code = self._release_integer(request, "X-Knoa-Version-Code")
            min_version_code = self._release_integer(request, "X-Knoa-Min-Version-Code")
            release_notes = self._release_notes(request)
        except ValueError:
            return JSONResponse({"error": "invalid_release_metadata"}, status_code=400)

        upload_root = self.root / ".mobile-release-uploads"
        upload_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        upload_root.chmod(0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="android-", suffix=".apk", dir=upload_root
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        received = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > content_length or received > _MAX_REMOTE_APK_BYTES:
                        return JSONResponse(
                            {"error": "payload_too_large"}, status_code=413
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            if received != content_length or digest.hexdigest() != declared_sha256:
                return JSONResponse({"error": "upload_mismatch"}, status_code=400)
            try:
                release = await asyncio.to_thread(
                    self.mobile_releases.publish,
                    temporary,
                    version_name=version_name,
                    version_code=version_code,
                    min_supported_version_code=min_version_code,
                    release_notes=release_notes,
                )
            except (LookupError, OSError, ValueError) as exc:
                return JSONResponse(
                    {"error": "release_rejected", "detail": str(exc)},
                    status_code=409,
                )
        finally:
            temporary.unlink(missing_ok=True)
        return JSONResponse(
            android_release_payload(
                release,
                channel="hosted",
                download_path=(
                    f"/releases/android/{release.version_code}/"
                    f"{release.sha256}/knoa.apk"
                ),
            ),
            status_code=201,
        )

    async def download_android_release(
        self,
        request: Request,
    ) -> JSONResponse | FileResponse:
        raw_version_code = str(request.path_params.get("version_code", ""))
        requested_sha256 = str(request.path_params.get("sha256", "")).lower()
        if not raw_version_code.isascii() or not raw_version_code.isdecimal():
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if not re.fullmatch(r"[0-9a-f]{64}", requested_sha256):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        version_code = int(raw_version_code)
        if version_code < 1 or version_code > 2_100_000_000:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            release = self.mobile_releases.get(version_code)
            if release.sha256 != requested_sha256:
                raise LookupError
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return self._android_package(release, immutable=True)

    async def download_latest_android_release(
        self,
        _request: Request,
    ) -> JSONResponse | FileResponse:
        try:
            release = self.mobile_releases.latest()
        except LookupError:
            return JSONResponse({"error": "not_found"}, status_code=404)
        assert release is not None
        return self._android_package(release, immutable=False)

    def _android_package(
        self,
        release: AndroidRelease,
        *,
        immutable: bool,
    ) -> JSONResponse | FileResponse:
        try:
            package = self.mobile_releases.package_path(release)
            metadata = package.stat()
        except (LookupError, OSError):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(
            package,
            media_type="application/vnd.android.package-archive",
            filename=f"knoa-{release.version_name}.apk",
            stat_result=metadata,
            headers={
                "Cache-Control": (
                    "public, max-age=31536000, immutable"
                    if immutable
                    else "public, max-age=60, must-revalidate"
                ),
                "ETag": f'"{release.sha256}"',
                "X-Knoa-SHA256": release.sha256,
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def change_password(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse(request, HostedPasswordChangeRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            token = self._bearer(request)
            self.control.change_password(
                str(authenticated["account_id"]),
                parsed.current_password,
                parsed.new_password,
                current_token=token,
            )
        except PermissionError:
            return JSONResponse({"error": "password_rejected"}, status_code=401)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse({"changed": True})

    async def password_reset_grants(self, request: Request) -> JSONResponse:
        if not self._allow(request, "password-reset-grant", limit=10):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        if not self._bootstrap(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        parsed = await self._parse(request, HostedPasswordResetGrantRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            grant = self.control.create_password_reset_grant(
                parsed.login_identity,
                parsed.ttl_seconds,
            )
        except (LookupError, ValueError):
            return JSONResponse({"error": "account_not_found"}, status_code=404)
        return JSONResponse(grant.__dict__, status_code=201)

    async def reset_password(self, request: Request) -> JSONResponse:
        if not self._allow(request, "password-reset", limit=20):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        parsed = await self._parse(request, HostedPasswordResetRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            account = self.control.reset_password(**parsed.model_dump(mode="json"))
        except PermissionError:
            return JSONResponse({"error": "password_reset_rejected"}, status_code=401)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse(self._account_payload(account), status_code=201)

    async def workspaces(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        account_id = str(authenticated["account_id"])
        if request.method == "GET":
            return JSONResponse(
                {
                    "workspaces": self._workspace_payloads(
                        self.control.list_workspaces(account_id)
                    )
                }
            )
        parsed = await self._parse(request, HostedWorkspaceRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            workspace = self.control.create_workspace(
                account_id,
                parsed.display_name,
                kind=parsed.kind,
            )
            self.tenants.application(str(workspace["workspace_id"]))
        except (PermissionError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse(self._workspace_payload(workspace), status_code=201)

    async def workspace_members(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        account_id = str(authenticated["account_id"])
        workspace_id = str(request.path_params["workspace_id"])
        try:
            if request.method == "GET":
                members = self.control.list_workspace_members(account_id, workspace_id)
                return JSONResponse({"members": members})
            parsed = await self._parse(request, HostedWorkspaceMemberRequest)
            if isinstance(parsed, JSONResponse):
                return parsed
            member = self.control.add_workspace_member(
                account_id,
                workspace_id,
                parsed.login_identity,
                parsed.role,
            )
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except LookupError:
            return JSONResponse({"error": "account_not_found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse(member, status_code=201)

    async def workspace_member(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        try:
            self.control.remove_workspace_member(
                str(authenticated["account_id"]),
                str(request.path_params["workspace_id"]),
                str(request.path_params["account_id"]),
            )
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except LookupError:
            return JSONResponse({"error": "membership_not_found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse({"removed": True})

    async def workspace_owner_transfer(self, request: Request) -> JSONResponse:
        authenticated = self._account(request)
        if isinstance(authenticated, JSONResponse):
            return authenticated
        parsed = await self._parse(request, HostedWorkspaceOwnerTransferRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            self.control.transfer_workspace_ownership(
                str(authenticated["account_id"]),
                str(request.path_params["workspace_id"]),
                parsed.account_id,
            )
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except LookupError:
            return JSONResponse({"error": "membership_not_found"}, status_code=404)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse({"transferred": True})

    def _account(self, request: Request) -> dict[str, Any] | JSONResponse:
        try:
            return self.control.authenticate(self._bearer(request))
        except PermissionError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    def _bootstrap(self, request: Request) -> bool:
        supplied = hashlib.sha256(self._bearer(request).encode()).digest()
        return secrets.compare_digest(supplied, self._bootstrap_digest)

    def _release_publisher(self, request: Request) -> bool:
        if self._release_publish_digest is None:
            return False
        supplied = hashlib.sha256(self._bearer(request).encode()).digest()
        return secrets.compare_digest(supplied, self._release_publish_digest)

    @staticmethod
    def _release_version_name(request: Request) -> str:
        value = request.headers.get("X-Knoa-Version-Name", "").strip()
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}", value):
            raise ValueError("invalid version name")
        return value

    @staticmethod
    def _release_integer(request: Request, header: str) -> int:
        value = request.headers.get(header, "").strip()
        if not value.isascii() or not value.isdecimal():
            raise ValueError("invalid release integer")
        parsed = int(value)
        if parsed < 1 or parsed > 2_100_000_000:
            raise ValueError("invalid release integer")
        return parsed

    @staticmethod
    def _release_notes(request: Request) -> str:
        encoded = request.headers.get("X-Knoa-Release-Notes", "").strip()
        if not encoded:
            return ""
        if len(encoded) > 8_000 or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise ValueError("invalid release notes")
        padding = "=" * (-len(encoded) % 4)
        try:
            value = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("invalid release notes") from exc
        if len(value.encode("utf-8")) > _MAX_REMOTE_RELEASE_NOTES_BYTES:
            raise ValueError("invalid release notes")
        return value

    def _allow(self, request: Request, scope: str, *, limit: int) -> bool:
        forwarded = request.headers.get("CF-Connecting-IP", "").strip()
        remote = forwarded or (
            request.client.host if request.client is not None else "unknown"
        )
        return self._limiter.allow(f"{scope}:{remote}", limit=limit)

    def _account_payload(self, account: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(account["default_workspace_id"])
        return {
            **account,
            "hub_id": self.hub_id,
            "identity_issuer_id": self.hub_id,
            "deployment_mode": DEPLOYMENT_MODE,
            "workspace_id": workspace_id,
            "workspace_path": f"/workspaces/{workspace_id}",
            "workspaces": self._workspace_payloads(account["workspaces"]),
        }

    @staticmethod
    def _workspace_payload(workspace: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(workspace["workspace_id"])
        return {
            "workspace_id": workspace_id,
            "display_name": workspace["display_name"],
            "kind": workspace["kind"],
            "role": workspace.get("role", "owner"),
            "workspace_path": f"/workspaces/{workspace_id}",
        }

    def _workspace_payloads(self, workspaces) -> list[dict[str, Any]]:
        return [self._workspace_payload(dict(workspace)) for workspace in workspaces]

    @staticmethod
    def _bearer(request: Request) -> str:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        return token if scheme.casefold() == "bearer" else ""

    @staticmethod
    async def _parse(request: Request, model):
        body = await request.body()
        if len(body) > 64 * 1024:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            return model.model_validate_json(body)
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)


def create_hosted_hub_app(
    root: str | Path,
    *,
    hub_id: str,
    bootstrap_token: str,
    release_publish_token: str = "",
) -> Starlette:
    return HostedHubApplication(
        root,
        hub_id=hub_id,
        bootstrap_token=bootstrap_token,
        release_publish_token=release_publish_token,
    ).app


__all__ = [
    "AccountEnrollmentGrant",
    "HostedControlRepository",
    "HostedHubApplication",
    "HostedTenantDispatcher",
    "PasswordResetGrant",
    "create_hosted_hub_app",
]
