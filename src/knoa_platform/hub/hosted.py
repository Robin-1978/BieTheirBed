"""Multi-tenant Hosted Hub simulation built from isolated Workspace compositions."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from knoa_platform.hub.app import HubApplication
from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService
from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


class HostedAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    login_identity: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=128)


class HostedAccountRepository:
    """Hosted issuer account state; tenant business state stays per Workspace."""

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
                    subject_id TEXT PRIMARY KEY,
                    login_identity TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosted_personal_workspaces(
                    workspace_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosted_access_tokens(
                    token_digest TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, foreign_keys=True)

    def create_account(
        self,
        login_identity: str,
        display_name: str,
        *,
        token_ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> dict[str, Any]:
        login = login_identity.strip().casefold()
        name = display_name.strip()
        if not re.fullmatch(r"[^\s]{3,254}", login):
            raise ValueError("Hosted login identity is invalid")
        if not name:
            raise ValueError("Hosted display name is required")
        now = self._clock()
        subject_id = f"sub_{secrets.token_urlsafe(18)}"
        workspace_id = f"ws_{secrets.token_urlsafe(18)}"
        access_token = f"khs_{secrets.token_urlsafe(36)}"
        digest = hashlib.sha256(access_token.encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO hosted_accounts VALUES (?, ?, ?, 'active', ?)",
                    (subject_id, login, name, now),
                )
            except sqlite3.IntegrityError as exc:
                raise FileExistsError("Hosted account already exists") from exc
            db.execute(
                "INSERT INTO hosted_personal_workspaces VALUES (?, ?, ?, 'active', ?)",
                (workspace_id, subject_id, f"{name} 的 Personal Workspace", now),
            )
            db.execute(
                "INSERT INTO hosted_access_tokens VALUES (?, ?, ?, ?, NULL)",
                (digest, subject_id, now, now + token_ttl_seconds),
            )
        return {
            "subject_id": subject_id,
            "login_identity": login,
            "display_name": name,
            "workspace_id": workspace_id,
            "access_token": access_token,
            "expires_at": now + token_ttl_seconds,
        }

    def authenticate(self, token: str) -> dict[str, Any]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            row = db.execute(
                """SELECT a.*, w.workspace_id, w.display_name AS workspace_name,
                          t.expires_at
                   FROM hosted_access_tokens t
                   JOIN hosted_accounts a ON a.subject_id=t.subject_id
                   JOIN hosted_personal_workspaces w ON w.subject_id=a.subject_id
                   WHERE t.token_digest=? AND t.revoked_at IS NULL
                     AND t.expires_at>? AND a.state='active' AND w.state='active'""",
                (digest, self._clock()),
            ).fetchone()
        if row is None:
            raise PermissionError("Hosted account authentication rejected")
        return dict(row)

    def authenticate_workspace(self, token: str, workspace_id: str) -> str:
        account = self.authenticate(token)
        if account["workspace_id"] != workspace_id:
            raise PermissionError("Hosted Workspace authentication rejected")
        return str(account["subject_id"])

    def workspace_owner(self, workspace_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT a.*, w.workspace_id, w.display_name AS workspace_name
                   FROM hosted_personal_workspaces w
                   JOIN hosted_accounts a ON a.subject_id=w.subject_id
                   WHERE w.workspace_id=? AND w.state='active' AND a.state='active'""",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Hosted Workspace not found")
        return dict(row)

    def tenant_count(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM hosted_personal_workspaces WHERE state='active'"
            ).fetchone()
        return int(row["count"])


class HostedTenantDispatcher:
    """Route one URL-scoped Workspace to an isolated Hub composition."""

    def __init__(
        self,
        root: Path,
        *,
        hub_id: str,
        accounts: HostedAccountRepository,
    ) -> None:
        self._root = root
        self._hub_id = hub_id
        self._accounts = accounts
        self._applications: dict[str, Starlette] = {}

    def application(self, workspace_id: str) -> Starlette:
        existing = self._applications.get(workspace_id)
        if existing is not None:
            return existing
        owner = self._accounts.workspace_owner(workspace_id)
        tenant_root = self._root / "tenants" / workspace_id
        repository = HubRepository(
            tenant_root / "hub.db",
            hub_id=workspace_id,
        )
        service = HubService(
            repository,
            self._root / "hub-signing.key",
            owner_subject_id=str(owner["subject_id"]),
            owner_authenticator=lambda token: self._accounts.authenticate_workspace(
                token,
                workspace_id,
            ),
            hub_id=self._hub_id,
        )
        application = HubApplication(
            service,
            deployment_mode="hosted_simulation",
        ).app
        self._applications[workspace_id] = application
        return application

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path", ""))
        relative = path.lstrip("/")
        if relative.startswith("workspaces/"):
            relative = relative.removeprefix("workspaces/")
        workspace_id, separator, remainder = relative.partition("/")
        if (
            not separator
            or not re.fullmatch(r"ws_[A-Za-z0-9_-]{12,96}", workspace_id)
        ):
            await JSONResponse({"error": "not_found"}, status_code=404)(
                scope,
                receive,
                send,
            )
            return
        try:
            application = self.application(workspace_id)
        except LookupError:
            await JSONResponse({"error": "not_found"}, status_code=404)(
                scope,
                receive,
                send,
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
    """Hosted simulation root: account issuer plus isolated Personal Workspaces."""

    def __init__(
        self,
        root: str | Path,
        *,
        hub_id: str,
        bootstrap_token: str,
    ) -> None:
        if len(bootstrap_token) < 32:
            raise ValueError("Hosted bootstrap token must contain at least 32 characters")
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.hub_id = hub_id
        self._bootstrap_digest = hashlib.sha256(bootstrap_token.encode()).digest()
        self.accounts = HostedAccountRepository(
            self.root / "accounts.db",
            hub_id=hub_id,
        )
        self.tenants = HostedTenantDispatcher(
            self.root,
            hub_id=hub_id,
            accounts=self.accounts,
        )
        self.app = Starlette(
            routes=[
                Route("/health", self.health, methods=["GET"]),
                Route("/v1/hosted/accounts", self.create_account, methods=["POST"]),
                Route("/v1/hosted/account", self.account, methods=["GET"]),
                Mount("/workspaces", app=self.tenants),
            ]
        )

    async def health(self, _request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "hub_id": self.hub_id,
                "deployment_mode": "hosted_simulation",
                "tenant_count": self.accounts.tenant_count(),
            }
        )

    async def create_account(self, request: Request) -> JSONResponse:
        if not self._bootstrap(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        parsed = await self._parse(request, HostedAccountRequest)
        if isinstance(parsed, JSONResponse):
            return parsed
        try:
            account = self.accounts.create_account(
                parsed.login_identity,
                parsed.display_name,
            )
        except FileExistsError:
            return JSONResponse({"error": "account_exists"}, status_code=409)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        workspace_id = str(account["workspace_id"])
        self.tenants.application(workspace_id)
        return JSONResponse(
            {
                **account,
                "hub_id": self.hub_id,
                "identity_issuer_id": self.hub_id,
                "deployment_mode": "hosted_simulation",
                "workspace_path": f"/workspaces/{workspace_id}",
            },
            status_code=201,
        )

    async def account(self, request: Request) -> JSONResponse:
        try:
            account = self.accounts.authenticate(self._bearer(request))
        except PermissionError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(
            {
                "subject_id": account["subject_id"],
                "login_identity": account["login_identity"],
                "display_name": account["display_name"],
                "workspace_id": account["workspace_id"],
                "workspace_name": account["workspace_name"],
                "expires_at": account["expires_at"],
                "hub_id": self.hub_id,
                "identity_issuer_id": self.hub_id,
                "deployment_mode": "hosted_simulation",
            }
        )

    def _bootstrap(self, request: Request) -> bool:
        supplied = hashlib.sha256(self._bearer(request).encode()).digest()
        return secrets.compare_digest(supplied, self._bootstrap_digest)

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
) -> Starlette:
    return HostedHubApplication(
        root,
        hub_id=hub_id,
        bootstrap_token=bootstrap_token,
    ).app


__all__ = [
    "HostedAccountRepository",
    "HostedHubApplication",
    "HostedTenantDispatcher",
    "create_hosted_hub_app",
]
