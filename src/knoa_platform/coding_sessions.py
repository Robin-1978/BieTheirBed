"""External coding-session catalog and KNOA binding store."""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from knoa_codex_agent.coding_sessions import (
    CodexAppServerProvider,
    CodingSessionConflict,
    CodingSessionDetail,
    CodingSessionPage,
    CodingSessionProvider,
    CodingSessionProviderError,
    CodingSessionSummary,
    CodingSessionTurnReceipt,
    OpenCodeHttpProvider,
)
from knoa_platform.opencode_service import OpenCodeServiceLocator


@dataclass(frozen=True, slots=True)
class CodingSessionBinding:
    binding_id: str
    principal_id: str
    session_handle: str
    provider: str
    external_session_id: str
    directory: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class CodingSessionListResult:
    sessions: tuple[CodingSessionSummary, ...]
    next_cursor: str = ""
    provider_errors: tuple[tuple[str, str], ...] = ()


class CodingSessionBindingRepository:
    """Persist which external session belongs to each KNOA conversation."""

    def __init__(self, database: str | Path) -> None:
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS coding_session_bindings ("
                "binding_id TEXT PRIMARY KEY,"
                "principal_id TEXT NOT NULL,"
                "session_handle TEXT NOT NULL,"
                "provider TEXT NOT NULL,"
                "external_session_id TEXT NOT NULL,"
                "directory TEXT NOT NULL DEFAULT '',"
                "created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL,"
                "UNIQUE(principal_id, session_handle),"
                "UNIQUE(principal_id, provider, external_session_id)"
                ")"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS coding_session_bindings_by_external "
                "ON coding_session_bindings(principal_id, provider, updated_at DESC)"
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> CodingSessionBinding:
        return CodingSessionBinding(
            binding_id=str(row["binding_id"]),
            principal_id=str(row["principal_id"]),
            session_handle=str(row["session_handle"]),
            provider=str(row["provider"]),
            external_session_id=str(row["external_session_id"]),
            directory=str(row["directory"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def attach(
        self,
        *,
        principal_id: str,
        session_handle: str,
        provider: str,
        external_session_id: str,
        directory: str,
    ) -> CodingSessionBinding:
        now = time.time()
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM coding_session_bindings "
                "WHERE principal_id=? AND session_handle=?",
                (principal_id, session_handle),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if (
                    record.provider != provider
                    or record.external_session_id != external_session_id
                ):
                    raise CodingSessionConflict(
                        "KNOA conversation is already bound to another coding session"
                    )
                db.execute(
                    "UPDATE coding_session_bindings SET directory=?, updated_at=? "
                    "WHERE binding_id=?",
                    (directory, now, record.binding_id),
                )
                return self.get_by_session(principal_id, session_handle)
            external = db.execute(
                "SELECT * FROM coding_session_bindings "
                "WHERE principal_id=? AND provider=? AND external_session_id=?",
                (principal_id, provider, external_session_id),
            ).fetchone()
            if external is not None:
                # This conversation has no binding yet (checked above), so the
                # external session belongs to a different KNOA conversation.
                raise CodingSessionConflict(
                    "Coding session is already bound to another KNOA conversation"
                )
            binding_id = f"cbind-{uuid.uuid4().hex}"
            db.execute(
                "INSERT INTO coding_session_bindings("
                "binding_id, principal_id, session_handle, provider, "
                "external_session_id, directory, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    binding_id,
                    principal_id,
                    session_handle,
                    provider,
                    external_session_id,
                    directory,
                    now,
                    now,
                ),
            )
        return self.get_by_session(principal_id, session_handle)

    def get_by_session(
        self, principal_id: str, session_handle: str
    ) -> CodingSessionBinding:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM coding_session_bindings "
                "WHERE principal_id=? AND session_handle=?",
                (principal_id, session_handle),
            ).fetchone()
        if row is None:
            raise LookupError("Coding-session binding not found")
        return self._record(row)

    def get_external(
        self, principal_id: str, provider: str, external_session_id: str
    ) -> CodingSessionBinding:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM coding_session_bindings "
                "WHERE principal_id=? AND provider=? AND external_session_id=?",
                (principal_id, provider, external_session_id),
            ).fetchone()
        if row is None:
            raise LookupError("Coding-session binding not found")
        return self._record(row)


class CodingSessionService:
    """Fan out across providers while enforcing bindings and directory policy."""

    def __init__(
        self,
        providers: Mapping[str, CodingSessionProvider],
        bindings: CodingSessionBindingRepository,
        *,
        allowed_directories: tuple[str, ...] = (),
    ) -> None:
        self._providers = dict(providers)
        self._bindings = bindings
        self._allowed_directories = tuple(
            Path(item).expanduser().resolve()
            for item in allowed_directories
            if item.strip()
        )

    @property
    def bindings(self) -> CodingSessionBindingRepository:
        return self._bindings

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    async def list_sessions(
        self,
        *,
        provider: str = "",
        directory: str = "",
        search: str = "",
        limit: int = 50,
        cursor: str = "",
        include_archived: bool = False,
    ) -> CodingSessionListResult:
        if provider and provider not in self._providers:
            raise CodingSessionProviderError(f"Unknown provider: {provider}")
        selected = (
            [(provider, self._providers[provider])]
            if provider
            else sorted(self._providers.items())
        )
        if directory and not self._directory_allowed(directory):
            raise PermissionError("Coding session directory is not allowed")
        results = await asyncio.gather(
            *(
                item.list_sessions(
                    directory=directory,
                    search=search,
                    limit=limit,
                    cursor=cursor,
                    include_archived=include_archived,
                )
                for _provider_id, item in selected
            ),
            return_exceptions=True,
        )
        pages: list[CodingSessionPage] = []
        errors: list[tuple[str, str]] = []
        for (provider_id, _item), result in zip(selected, results, strict=True):
            if isinstance(result, BaseException):
                errors.append((provider_id, str(result)))
            else:
                pages.append(result)
        allowed = [
            session
            for page in pages
            for session in page.sessions
            if self._directory_allowed(session.directory)
        ]
        allowed.sort(key=lambda item: item.updated_at, reverse=True)
        next_cursor = pages[0].next_cursor if len(pages) == 1 else ""
        return CodingSessionListResult(
            sessions=tuple(allowed[: max(1, min(limit, 200))]),
            next_cursor=next_cursor,
            provider_errors=tuple(errors),
        )

    async def get_session(
        self, provider: str, external_session_id: str
    ) -> CodingSessionDetail:
        item = self._provider(provider)
        detail = await item.get_session(external_session_id)
        if not self._directory_allowed(detail.session.directory):
            raise PermissionError("Coding session directory is not allowed")
        return detail

    async def attach(
        self,
        *,
        principal_id: str,
        session_handle: str,
        provider: str,
        external_session_id: str,
    ) -> CodingSessionBinding:
        detail = await self.get_session(provider, external_session_id)
        return await asyncio.to_thread(
            self._bindings.attach,
            principal_id=principal_id,
            session_handle=session_handle,
            provider=provider,
            external_session_id=external_session_id,
            directory=detail.session.directory,
        )

    async def continue_session(
        self,
        *,
        principal_id: str,
        provider: str,
        external_session_id: str,
        text: str,
    ) -> CodingSessionTurnReceipt:
        self._require_binding(
            principal_id, provider, external_session_id, check_handle=False
        )
        detail = await self.get_session(provider, external_session_id)
        if detail.session.read_only:
            raise CodingSessionConflict("Coding session is active in another client")
        return await self._provider(provider).continue_session(
            external_session_id, text
        )

    async def interrupt_session(
        self,
        *,
        principal_id: str,
        provider: str,
        external_session_id: str,
        external_turn_id: str = "",
    ) -> bool:
        self._require_binding(
            principal_id, provider, external_session_id, check_handle=False
        )
        detail = await self.get_session(provider, external_session_id)
        if "interrupt" not in detail.session.capabilities:
            # design.md: interrupt is only sent when the provider declares it.
            raise CodingSessionConflict(
                "Coding session has no running turn this service can interrupt"
            )
        return await self._provider(provider).interrupt_session(
            external_session_id, external_turn_id
        )

    def _require_binding(
        self,
        principal_id: str,
        provider: str,
        external_session_id: str,
        *,
        check_handle: bool,
    ) -> CodingSessionBinding:
        binding = self._bindings.get_external(
            principal_id, provider, external_session_id
        )
        if check_handle and not binding.session_handle:
            raise PermissionError("Coding session is not attached")
        return binding

    def _provider(self, provider: str) -> CodingSessionProvider:
        try:
            return self._providers[provider]
        except KeyError as exc:
            raise CodingSessionProviderError(f"Unknown provider: {provider}") from exc

    def _directory_allowed(self, directory: str) -> bool:
        if not self._allowed_directories:
            return True
        try:
            candidate = Path(directory).expanduser().resolve()
        except OSError:
            return False
        return any(
            candidate == allowed or allowed in candidate.parents
            for allowed in self._allowed_directories
        )

    async def close(self) -> None:
        await asyncio.gather(
            *(provider.close() for provider in self._providers.values()),
            return_exceptions=True,
        )


def build_coding_session_service(config, database: str | Path) -> CodingSessionService:
    """Build the host-side bridge from private application configuration.

    The bridge works in the environment of the OS user that runs KNOA: Codex
    is always represented (its app-server executable is started lazily on the
    first request) and OpenCode is followed through its own service
    registration, so no second account or password is needed.  An explicit
    ``opencode_server_url`` + password overrides that discovery.  Without
    ``coding_session_allowed_directories`` every session of the current user is
    visible; the setting only exists to narrow that down.  A missing optional
    service cannot prevent KNOA from starting.
    """

    bindings = CodingSessionBindingRepository(database)
    if not getattr(config, "coding_sessions_enabled", True):
        return CodingSessionService({}, bindings)
    command = tuple(
        getattr(config, "codex_app_server_command", ("codex", "app-server"))
    )
    providers: dict[str, CodingSessionProvider] = {
        "codex": CodexAppServerProvider(
            command=command or ("codex", "app-server"),
            cwd=str(getattr(config, "codex_app_server_cwd", "") or ""),
        )
    }
    opencode_url = str(getattr(config, "opencode_server_url", "") or "").strip()
    opencode_password = getattr(config, "opencode_server_password", "")
    if hasattr(opencode_password, "get_secret_value"):
        opencode_password = opencode_password.get_secret_value()
    opencode_directory = str(getattr(config, "opencode_server_directory", "") or "")
    if opencode_url:
        # An explicit endpoint is never mixed with discovery: without its
        # password the provider is simply not offered.
        if str(opencode_password):
            providers["opencode"] = OpenCodeHttpProvider(
                opencode_url,
                username=str(getattr(config, "opencode_server_username", "opencode")),
                password=str(opencode_password),
                directory=opencode_directory,
            )
    elif getattr(config, "opencode_service_discovery", True):
        providers["opencode"] = OpenCodeHttpProvider(
            locator=OpenCodeServiceLocator(), directory=opencode_directory
        )
    allowed = tuple(getattr(config, "coding_session_allowed_directories", ()) or ())
    return CodingSessionService(providers, bindings, allowed_directories=allowed)
