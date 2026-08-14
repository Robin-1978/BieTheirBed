"""Jira REST adapter and local durable state for the MCP example server."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*$")
_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TRANSITION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
    }
)
_RASTER_IMAGE_MIME_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)
_IMAGE_FORMAT_FOR_MIME = {
    "image/gif": "GIF",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_MAX_ANALYSIS_PROMPT_BYTES = 64 * 1024
logger = logging.getLogger("jira-mcp-example.client")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable is not set: {name}")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _optional_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path == Path(path.anchor):
        raise ValueError(f"{name} must not be a filesystem root")
    return path


@dataclass(frozen=True)
class JiraSettings:
    base_url: str
    username: str
    api_token: str
    auth_mode: str
    api_version: str
    jql: str
    poll_interval_seconds: int
    retention_days: int
    max_issues: int
    state_path: Path
    attachment_root: Path
    code_root: Path | None
    log_root: Path | None
    analysis_prompt_path: Path | None
    max_attachment_bytes: int
    write_enabled: bool

    @classmethod
    def from_env(cls) -> JiraSettings:
        base_url = _required_env("JIRA_BASE_URL").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("JIRA_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("JIRA_BASE_URL must not contain credentials or fragments")
        auth_mode = os.environ.get("JIRA_AUTH_MODE", "basic").strip().lower()
        if auth_mode not in {"basic", "bearer"}:
            raise ValueError("JIRA_AUTH_MODE must be basic or bearer")
        username = os.environ.get("JIRA_USERNAME", "").strip()
        if auth_mode == "basic" and not username:
            raise ValueError(
                "Required environment variable is not set for basic auth: "
                "JIRA_USERNAME"
            )
        api_version = os.environ.get("JIRA_API_VERSION", "2").strip()
        if api_version not in {"2", "3"}:
            raise ValueError("JIRA_API_VERSION must be 2 or 3")
        state_path = (
            Path(
                os.environ.get(
                    "JIRA_MCP_STATE_PATH",
                    "~/.knoa/data/jira-mcp-example.db",
                )
            )
            .expanduser()
            .resolve()
        )
        attachment_root = (
            Path(
                os.environ.get(
                    "JIRA_ATTACHMENT_ROOT",
                    "~/.knoa/jira-evidence",
                )
            )
            .expanduser()
            .resolve()
        )
        if attachment_root == Path(attachment_root.anchor):
            raise ValueError("JIRA_ATTACHMENT_ROOT must not be a filesystem root")
        return cls(
            base_url=base_url,
            username=username,
            api_token=_required_env("JIRA_API_TOKEN"),
            auth_mode=auth_mode,
            api_version=api_version,
            jql=os.environ.get(
                "JIRA_JQL",
                "assignee = currentUser() AND statusCategory != Done "
                "ORDER BY updated DESC",
            ).strip(),
            poll_interval_seconds=_bounded_int(
                "JIRA_POLL_INTERVAL_SECONDS", 60, 10, 3600
            ),
            retention_days=_bounded_int("JIRA_EVENT_RETENTION_DAYS", 7, 1, 365),
            max_issues=_bounded_int("JIRA_MAX_ISSUES", 100, 1, 500),
            state_path=state_path,
            attachment_root=attachment_root,
            code_root=_optional_path("JIRA_CODE_ROOT"),
            log_root=_optional_path("JIRA_LOG_ROOT"),
            analysis_prompt_path=_optional_path("JIRA_ANALYSIS_PROMPT_PATH"),
            max_attachment_bytes=_bounded_int(
                "JIRA_MAX_ATTACHMENT_BYTES",
                100 * 1024 * 1024,
                1024,
                2 * 1024 * 1024 * 1024,
            ),
            write_enabled=os.environ.get("JIRA_WRITE_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )

    def analysis_instructions(self) -> str:
        path = self.analysis_prompt_path
        if path is None:
            return (
                "Correlate Jira evidence, logs and source code. Produce a problem "
                "summary, evidence, likely root cause, affected code, verification "
                "plan, remediation proposal and Jira comment draft. Do not write a "
                "Jira comment or modify source code without explicit user approval."
            )
        if path.is_symlink() or not path.is_file():
            raise ValueError("JIRA_ANALYSIS_PROMPT_PATH must be a regular file")
        data = path.read_bytes()
        if len(data) > _MAX_ANALYSIS_PROMPT_BYTES:
            raise ValueError(
                f"JIRA analysis prompt exceeds {_MAX_ANALYSIS_PROMPT_BYTES} bytes"
            )
        try:
            prompt = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("Jira analysis prompt must be UTF-8 text") from exc
        if not prompt:
            raise ValueError("Jira analysis prompt must not be empty")
        return prompt


def validate_issue_key(issue_key: str) -> str:
    normalized = issue_key.strip().upper()
    if not _ISSUE_KEY.fullmatch(normalized):
        raise ValueError("Invalid Jira issue key")
    return normalized


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_plain_text(item) for item in value)
    if isinstance(value, dict):
        node_type = value.get("type")
        if node_type == "text":
            return str(value.get("text", ""))
        separator = "\n" if node_type in {"paragraph", "heading", "listItem"} else ""
        return _plain_text(value.get("content", [])) + separator
    return str(value)


def _user_identity(user: Any) -> set[str]:
    if not isinstance(user, dict):
        return set()
    return {
        str(value).casefold()
        for key in ("accountId", "name", "key", "emailAddress", "displayName")
        if (value := user.get(key))
    }


class JiraStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS assignment_events (
                    event_id TEXT PRIMARY KEY,
                    issue_key TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    detected_at REAL NOT NULL,
                    retained_until REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS assignment_events_by_retention
                    ON assignment_events(retained_until, event_id);
                CREATE TABLE IF NOT EXISTS assignment_sources (
                    source_id TEXT PRIMARY KEY,
                    initialized_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observed_assignments (
                    source_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    PRIMARY KEY(source_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS comment_actions (
                    idempotency_key TEXT PRIMARY KEY,
                    issue_key TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    state TEXT NOT NULL,
                    comment_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(assignment_events)")
            }
            if "snapshot_json" not in columns:
                db.execute(
                    "ALTER TABLE assignment_events "
                    "ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{}'"
                )

    def assignment_source_initialized(self, source_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM assignment_sources WHERE source_id=?",
                (source_id,),
            ).fetchone()
        return row is not None

    def initialize_assignment_source(
        self,
        source_id: str,
        assignments: tuple[tuple[str, str], ...],
    ) -> None:
        now = time.time()
        with self._connect() as db:
            db.executemany(
                """INSERT OR IGNORE INTO observed_assignments(
                       source_id, event_id, issue_key, observed_at
                   ) VALUES (?, ?, ?, ?)""",
                ((source_id, event_id, issue_key, now) for event_id, issue_key in assignments),
            )
            db.execute(
                "INSERT OR IGNORE INTO assignment_sources(source_id, initialized_at) VALUES (?, ?)",
                (source_id, now),
            )

    def assignment_observed(self, source_id: str, event_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                """SELECT 1 FROM observed_assignments
                   WHERE source_id=? AND event_id=?""",
                (source_id, event_id),
            ).fetchone()
        return row is not None

    def record_observed_assignment(
        self,
        source_id: str,
        event_id: str,
        issue_key: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO observed_assignments(
                       source_id, event_id, issue_key, observed_at
                   ) VALUES (?, ?, ?, ?)""",
                (source_id, event_id, issue_key, time.time()),
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        self.path.chmod(0o600)
        return db

    def add_assignment_event(
        self,
        event_id: str,
        issue_key: str,
        *,
        retention_seconds: float,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO assignment_events(
                       event_id, issue_key, snapshot_json, detected_at, retained_until
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    event_id,
                    issue_key,
                    json.dumps(snapshot or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    now + retention_seconds,
                ),
            )
            return cursor.rowcount == 1

    def cleanup_events(self) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM assignment_events WHERE retained_until < ?",
                (time.time(),),
            )

    def list_assignment_events(self) -> tuple[dict[str, Any], ...]:
        self.cleanup_events()
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, issue_key, snapshot_json, detected_at
                   FROM assignment_events
                   ORDER BY detected_at, event_id"""
            ).fetchall()
        return tuple(
            {
                **dict(row),
                "snapshot": json.loads(row["snapshot_json"]),
            }
            for row in rows
        )

    def get_assignment_event(self, event_id: str) -> dict[str, Any] | None:
        self.cleanup_events()
        with self._connect() as db:
            row = db.execute(
                """SELECT event_id, issue_key, snapshot_json, detected_at
                   FROM assignment_events WHERE event_id=?""",
                (event_id,),
            ).fetchone()
        return None if row is None else {
            **dict(row),
            "snapshot": json.loads(row["snapshot_json"]),
        }

    def get_comment_action(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM comment_actions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else dict(row)

    def begin_comment_action(
        self,
        idempotency_key: str,
        issue_key: str,
        body_hash: str,
        marker: str,
    ) -> tuple[dict[str, Any], bool]:
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO comment_actions(
                       idempotency_key, issue_key, body_hash, marker,
                       state, comment_id, updated_at
                   ) VALUES (?, ?, ?, ?, 'pending', '', ?)""",
                (idempotency_key, issue_key, body_hash, marker, now),
            )
            row = db.execute(
                "SELECT * FROM comment_actions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        assert row is not None
        action = dict(row)
        if action["issue_key"] != issue_key or action["body_hash"] != body_hash:
            raise ValueError("Idempotency key was already used for another comment")
        return action, cursor.rowcount == 1

    def update_comment_action(
        self,
        idempotency_key: str,
        state: str,
        *,
        comment_id: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE comment_actions
                   SET state=?, comment_id=?, updated_at=?
                   WHERE idempotency_key=?""",
                (state, comment_id, time.time(), idempotency_key),
            )


class JiraClient:
    def __init__(self, settings: JiraSettings, store: JiraStateStore) -> None:
        self.settings = settings
        self.store = store
        headers = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if settings.auth_mode == "basic":
            auth = httpx.BasicAuth(settings.username, settings.api_token)
        else:
            headers["Authorization"] = f"Bearer {settings.api_token}"
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers=headers,
            auth=auth,
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )
        self._current_user_ids: set[str] = set()
        self._comment_locks: dict[str, asyncio.Lock] = {}
        self._materialize_locks: dict[str, asyncio.Lock] = {}

    @property
    def api_root(self) -> str:
        return f"/rest/api/{self.settings.api_version}"

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def current_user_ids(self) -> set[str]:
        if not self._current_user_ids:
            user = await self._request("GET", f"{self.api_root}/myself")
            self._current_user_ids = _user_identity(user)
            self._current_user_ids.add(self.settings.username.casefold())
        return set(self._current_user_ids)

    async def search_assigned_issues(self) -> tuple[dict[str, Any], ...]:
        payload = await self._request(
            "GET",
            f"{self.api_root}/search",
            params={
                "jql": self.settings.jql,
                "startAt": 0,
                "maxResults": self.settings.max_issues,
                "fields": "summary,status,priority,issuetype,assignee,updated,created",
                "expand": "changelog",
            },
        )
        issues = payload.get("issues", []) if isinstance(payload, dict) else []
        return tuple(issue for issue in issues if isinstance(issue, dict))

    async def poll_assignment_events(self) -> tuple[dict[str, str], ...]:
        current_user_ids = await self.current_user_ids()
        source_identity = json.dumps(
            {
                "base_url": self.settings.base_url,
                "username": self.settings.username.casefold(),
                "jql": self.settings.jql,
                "max_issues": self.settings.max_issues,
            },
            sort_keys=True,
        )
        source_id = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()[:32]
        candidates: list[tuple[str, str]] = []
        for issue in await self.search_assigned_issues():
            issue_key = validate_issue_key(str(issue.get("key", "")))
            event_id = self._assignment_event_id(issue, current_user_ids)
            candidates.append((event_id, issue_key))
        if not self.store.assignment_source_initialized(source_id):
            self.store.initialize_assignment_source(source_id, tuple(candidates))
            return ()

        created: list[dict[str, str]] = []
        retention = self.settings.retention_days * 24 * 60 * 60
        for event_id, issue_key in candidates:
            if self.store.assignment_observed(source_id, event_id):
                continue
            if self.store.get_assignment_event(event_id) is not None:
                self.store.record_observed_assignment(source_id, event_id, issue_key)
                continue
            try:
                evidence = await self.materialize_issue(issue_key)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Jira evidence materialization failed for %s", issue_key)
                continue
            if self.store.add_assignment_event(
                event_id,
                issue_key,
                retention_seconds=retention,
                snapshot=(
                    evidence.get("snapshot")
                    if isinstance(evidence.get("snapshot"), dict)
                    else {}
                ),
            ):
                created.append(
                    {
                        "event_id": event_id,
                        "issue_key": issue_key,
                        "evidence_directory": str(evidence["evidence_directory"]),
                        "manifest": str(evidence["manifest"]),
                    }
                )
            self.store.record_observed_assignment(source_id, event_id, issue_key)
        return tuple(created)

    @staticmethod
    def _assignment_event_id(
        issue: dict[str, Any], current_user_ids: set[str]
    ) -> str:
        issue_key = validate_issue_key(str(issue.get("key", "")))
        changelog = issue.get("changelog")
        histories = (
            changelog.get("histories", []) if isinstance(changelog, dict) else []
        )
        matching_history_id = ""
        for history in reversed(histories):
            if not isinstance(history, dict):
                continue
            for item in history.get("items", []):
                if not isinstance(item, dict) or item.get("field") != "assignee":
                    continue
                target = {
                    str(item.get(name, "")).casefold()
                    for name in ("to", "toString")
                    if item.get(name)
                }
                if target & current_user_ids:
                    matching_history_id = str(history.get("id", ""))
                    break
            if matching_history_id:
                break
        if matching_history_id:
            identity = f"assignment:{issue_key}:{matching_history_id}"
        else:
            fields = issue.get("fields", {})
            identity = f"initial:{issue.get('id', issue_key)}:{fields.get('created', '')}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    async def get_issue(
        self, issue_key: str, *, changelog: bool = False
    ) -> dict[str, Any]:
        key = validate_issue_key(issue_key)
        params: dict[str, Any] = {
            "fields": (
                "summary,description,status,priority,issuetype,assignee,reporter,"
                "created,updated,labels,components,attachment,comment"
            )
        }
        if changelog:
            params["expand"] = "changelog"
        issue = await self._request(
            "GET", f"{self.api_root}/issue/{key}", params=params
        )
        if not isinstance(issue, dict):
            raise TypeError("Jira returned an invalid issue response")
        fields = (
            issue.get("fields", {}) if isinstance(issue.get("fields"), dict) else {}
        )
        return {
            "key": key,
            "summary": str(fields.get("summary", ""))[:2000],
            "description": _plain_text(fields.get("description"))[:50_000],
            "status": _named(fields.get("status")),
            "priority": _named(fields.get("priority")),
            "issue_type": _named(fields.get("issuetype")),
            "assignee": _display_user(fields.get("assignee")),
            "reporter": _display_user(fields.get("reporter")),
            "created": str(fields.get("created", "")),
            "updated": str(fields.get("updated", "")),
            "labels": tuple(str(item) for item in fields.get("labels", [])[:100]),
            "components": tuple(
                _named(item) for item in fields.get("components", [])[:100]
            ),
            "changelog": issue.get("changelog") if changelog else None,
        }

    async def get_comments(
        self, issue_key: str, *, limit: int = 50
    ) -> tuple[dict[str, Any], ...]:
        key = validate_issue_key(issue_key)
        bounded_limit = max(1, min(limit, 100))
        payload = await self._request(
            "GET",
            f"{self.api_root}/issue/{key}/comment",
            params={"startAt": 0, "maxResults": bounded_limit, "orderBy": "-created"},
        )
        comments = payload.get("comments", []) if isinstance(payload, dict) else []
        rendered: list[dict[str, Any]] = []
        for comment in comments[:bounded_limit]:
            if not isinstance(comment, dict):
                continue
            rendered.append(
                {
                    "id": str(comment.get("id", "")),
                    "author": _display_user(comment.get("author")),
                    "created": str(comment.get("created", "")),
                    "updated": str(comment.get("updated", "")),
                    "body": _plain_text(comment.get("body"))[:20_000],
                }
            )
        return tuple(rendered)

    async def find_assignable_users(
        self,
        issue_key: str,
        query: str,
        *,
        limit: int = 20,
    ) -> tuple[dict[str, str], ...]:
        key = validate_issue_key(issue_key)
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > 256:
            raise ValueError("Jira user query must contain 1-256 characters")
        bounded_limit = max(1, min(limit, 50))
        query_name = "query" if self.settings.api_version == "3" else "username"
        payload = await self._request(
            "GET",
            f"{self.api_root}/user/assignable/search",
            params={
                "issueKey": key,
                query_name: normalized_query,
                "maxResults": bounded_limit,
            },
        )
        users = payload if isinstance(payload, list) else []
        return tuple(
            {
                "id": str(
                    user.get("accountId")
                    or user.get("name")
                    or user.get("key")
                    or ""
                ),
                "display_name": str(user.get("displayName", ""))[:1000],
                "email": str(user.get("emailAddress", ""))[:1000],
            }
            for user in users[:bounded_limit]
            if isinstance(user, dict)
        )

    async def assign_issue(self, issue_key: str, assignee_id: str) -> dict[str, Any]:
        if not self.settings.write_enabled:
            raise PermissionError("Jira writes are disabled")
        key = validate_issue_key(issue_key)
        normalized_assignee = assignee_id.strip()
        if not normalized_assignee or len(normalized_assignee) > 256:
            raise ValueError("Jira assignee ID must contain 1-256 characters")
        identity_field = "accountId" if self.settings.api_version == "3" else "name"
        await self._request(
            "PUT",
            f"{self.api_root}/issue/{key}/assignee",
            json={identity_field: normalized_assignee},
        )
        return {
            "status": "succeeded",
            "issue_key": key,
            "assignee_id": normalized_assignee,
        }

    async def list_transitions(self, issue_key: str) -> tuple[dict[str, Any], ...]:
        key = validate_issue_key(issue_key)
        payload = await self._request(
            "GET",
            f"{self.api_root}/issue/{key}/transitions",
            params={"expand": "transitions.fields"},
        )
        transitions = payload.get("transitions", []) if isinstance(payload, dict) else []
        rendered: list[dict[str, Any]] = []
        for transition in transitions[:100]:
            if not isinstance(transition, dict):
                continue
            fields = transition.get("fields", {})
            rendered_fields: dict[str, Any] = {}
            if isinstance(fields, dict):
                for field_id, field in list(fields.items())[:100]:
                    if not isinstance(field, dict):
                        continue
                    schema = field.get("schema", {})
                    allowed = field.get("allowedValues", [])
                    rendered_fields[str(field_id)] = {
                        "name": str(field.get("name", field_id))[:1000],
                        "required": bool(field.get("required", False)),
                        "type": str(schema.get("type", ""))
                        if isinstance(schema, dict)
                        else "",
                        "allowed_values": tuple(
                            _transition_allowed_value(value)
                            for value in allowed[:100]
                        )
                        if isinstance(allowed, list)
                        else (),
                    }
            rendered.append(
                {
                    "id": str(transition.get("id", "")),
                    "name": str(transition.get("name", ""))[:1000],
                    "target_status": _named(transition.get("to")),
                    "fields": rendered_fields,
                }
            )
        return tuple(rendered)

    async def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        *,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.write_enabled:
            raise PermissionError("Jira writes are disabled")
        key = validate_issue_key(issue_key)
        normalized_transition = transition_id.strip()
        if not _TRANSITION_ID.fullmatch(normalized_transition):
            raise ValueError("Invalid Jira transition ID")
        normalized_fields = dict(fields or {})
        transitions = await self.list_transitions(key)
        selected = next(
            (
                transition
                for transition in transitions
                if transition.get("id") == normalized_transition
            ),
            None,
        )
        if selected is None:
            raise LookupError("Jira transition is not currently available")
        declared_fields = selected.get("fields", {})
        if not isinstance(declared_fields, dict):
            declared_fields = {}
        unknown_fields = sorted(set(normalized_fields) - set(declared_fields))
        if unknown_fields:
            raise ValueError(
                f"Jira transition fields are not currently available: {unknown_fields[0]}"
            )
        missing_required = sorted(
            field_id
            for field_id, definition in declared_fields.items()
            if isinstance(definition, dict)
            and definition.get("required")
            and field_id not in normalized_fields
        )
        if missing_required:
            raise ValueError(
                f"Jira transition requires field: {missing_required[0]}"
            )
        encoded_fields = json.dumps(
            normalized_fields,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        if len(encoded_fields) > 64 * 1024:
            raise ValueError("Jira transition fields exceed 65536 bytes")
        payload: dict[str, Any] = {
            "transition": {"id": normalized_transition},
        }
        if normalized_fields:
            payload["fields"] = normalized_fields
        await self._request(
            "POST",
            f"{self.api_root}/issue/{key}/transitions",
            json=payload,
        )
        return {
            "status": "succeeded",
            "issue_key": key,
            "transition_id": normalized_transition,
        }

    async def list_attachments(self, issue_key: str) -> tuple[dict[str, Any], ...]:
        key = validate_issue_key(issue_key)
        attachments = await self._attachment_records(key)
        return tuple(
            {
                "id": str(item.get("id", "")),
                "filename": str(item.get("filename", ""))[:1000],
                "mime_type": str(item.get("mimeType", ""))[:256],
                "size": max(0, int(item.get("size", 0) or 0)),
                "created": str(item.get("created", "")),
            }
            for item in attachments[:100]
            if isinstance(item, dict)
        )

    async def _attachment_records(self, issue_key: str) -> tuple[dict[str, Any], ...]:
        key = validate_issue_key(issue_key)
        issue = await self._request(
            "GET",
            f"{self.api_root}/issue/{key}",
            params={"fields": "attachment"},
        )
        fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
        attachments = fields.get("attachment", []) if isinstance(fields, dict) else []
        return tuple(item for item in attachments[:100] if isinstance(item, dict))

    def _attachment(self, attachments: tuple[dict[str, Any], ...], attachment_id: str) -> dict[str, Any]:
        normalized_id = attachment_id.strip()
        if not _ATTACHMENT_ID.fullmatch(normalized_id):
            raise ValueError("Invalid Jira attachment ID")
        match = next(
            (item for item in attachments if str(item.get("id", "")) == normalized_id),
            None,
        )
        if match is None:
            raise LookupError("Jira attachment does not belong to the issue")
        return match

    def _attachment_url(self, attachment: dict[str, Any]) -> str:
        content_url = str(attachment.get("content", ""))
        target = urlsplit(urljoin(f"{self.settings.base_url}/", content_url))
        base = urlsplit(self.settings.base_url)
        if (
            target.username
            or target.password
            or target.scheme != base.scheme
            or target.hostname != base.hostname
            or target.port != base.port
            or target.fragment
        ):
            raise ValueError("Jira attachment URL is outside the configured Jira origin")
        return target.geturl()

    @staticmethod
    def _safe_filename(attachment_id: str, filename: str) -> str:
        basename = Path(filename.replace("\\", "/")).name.strip()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
        if not safe:
            safe = "attachment.bin"
        safe = safe[:180]
        return f"{attachment_id}-{safe}"

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        if path.is_symlink():
            raise ValueError("Jira evidence file must not be a symbolic link")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
                + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _private_directory(path: Path) -> None:
        if path.is_symlink():
            raise ValueError("Jira evidence directory must not be a symbolic link")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Jira evidence directory is invalid")
        path.chmod(0o700)

    async def download_attachment(
        self,
        issue_key: str,
        attachment_id: str,
        *,
        attachments: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, Any]:
        key = validate_issue_key(issue_key)
        records = attachments if attachments is not None else await self._attachment_records(key)
        attachment = self._attachment(records, attachment_id)
        normalized_id = str(attachment.get("id", "")).strip()
        declared_size = max(0, int(attachment.get("size", 0) or 0))
        if declared_size > self.settings.max_attachment_bytes:
            raise ValueError("Jira attachment exceeds JIRA_MAX_ATTACHMENT_BYTES")
        issue_root = self.settings.attachment_root / key
        attachment_root = issue_root / "attachments"
        self._private_directory(self.settings.attachment_root)
        self._private_directory(issue_root)
        self._private_directory(attachment_root)
        target = attachment_root / self._safe_filename(
            normalized_id,
            str(attachment.get("filename", "")),
        )
        if target.is_symlink():
            raise ValueError("Jira evidence file must not be a symbolic link")
        if target.exists():
            size = target.stat().st_size
            if size > self.settings.max_attachment_bytes:
                raise ValueError("Existing Jira attachment exceeds the configured limit")
            if declared_size and size != declared_size:
                raise ValueError("Existing Jira attachment size does not match Jira metadata")
            return self._download_result(attachment, target, reused=True)

        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        digest = hashlib.sha256()
        written = 0
        try:
            async with self._client.stream(
                "GET",
                self._attachment_url(attachment),
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        response_size = int(content_length)
                    except ValueError as exc:
                        raise ValueError("Jira attachment Content-Length is invalid") from exc
                    if response_size > self.settings.max_attachment_bytes:
                        raise ValueError("Jira attachment exceeds JIRA_MAX_ATTACHMENT_BYTES")
                with temporary.open("xb") as output:
                    temporary.chmod(0o600)
                    async for chunk in response.aiter_bytes():
                        written += len(chunk)
                        if written > self.settings.max_attachment_bytes:
                            raise ValueError("Jira attachment exceeds JIRA_MAX_ATTACHMENT_BYTES")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if declared_size and written != declared_size:
                raise ValueError("Downloaded Jira attachment size does not match Jira metadata")
            self._validate_downloaded_attachment(attachment, temporary)
            os.replace(temporary, target)
            target.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return self._download_result(
            attachment,
            target,
            reused=False,
            sha256=digest.hexdigest(),
        )

    @staticmethod
    def _validate_downloaded_attachment(
        attachment: dict[str, Any],
        path: Path,
    ) -> None:
        mime_type = str(attachment.get("mimeType", "")).lower()
        if mime_type not in _RASTER_IMAGE_MIME_TYPES:
            return
        try:
            from PIL import Image, UnidentifiedImageError

            with Image.open(path) as image:
                if image.format != _IMAGE_FORMAT_FOR_MIME[mime_type]:
                    raise ValueError(
                        "Jira image attachment format does not match its MIME type"
                    )
                image.verify()
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("Jira image attachment content is invalid") from exc

    @staticmethod
    def _download_result(
        attachment: dict[str, Any],
        path: Path,
        *,
        reused: bool,
        sha256: str = "",
    ) -> dict[str, Any]:
        if not sha256:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha256 = digest.hexdigest()
        return {
            "attachment_id": str(attachment.get("id", "")),
            "filename": str(attachment.get("filename", ""))[:1000],
            "mime_type": str(
                attachment.get("mimeType", "application/octet-stream")
            )[:256],
            "size": path.stat().st_size,
            "sha256": sha256,
            "path": str(path),
            "reused": reused,
        }

    async def materialize_issue(self, issue_key: str) -> dict[str, Any]:
        key = validate_issue_key(issue_key)
        lock = self._materialize_locks.setdefault(key, asyncio.Lock())
        async with lock:
            issue_root = self.settings.attachment_root / key
            self._private_directory(self.settings.attachment_root)
            self._private_directory(issue_root)
            issue = await self.get_issue(key, changelog=True)
            comments = await self.get_comments(key, limit=100)
            attachments = await self._attachment_records(key)
            downloaded: list[dict[str, Any]] = []
            for attachment in attachments:
                attachment_id = str(attachment.get("id", ""))
                downloaded.append(
                    await self.download_attachment(
                        key,
                        attachment_id,
                        attachments=attachments,
                    )
                )
            issue_path = issue_root / "issue.json"
            comments_path = issue_root / "comments.json"
            manifest_path = issue_root / "manifest.json"
            self._write_json(issue_path, issue)
            self._write_json(comments_path, {"comments": comments})
            manifest = {
                "format": "knoa-jira-evidence-v1",
                "issue_key": key,
                "materialized_at": time.time(),
                "evidence_directory": str(issue_root),
                "issue": str(issue_path),
                "comments": str(comments_path),
                "attachments": downloaded,
            }
            self._write_json(manifest_path, manifest)
            snapshot = {
                "issue": {
                    **issue,
                    "description": str(issue.get("description", ""))[:8_000],
                    "changelog": None,
                },
                "comments": [
                    {
                        **comment,
                        "body": str(comment.get("body", ""))[:2_000],
                    }
                    for comment in comments[:10]
                ],
                "attachments": [
                    {
                        key: attachment.get(key)
                        for key in (
                            "attachment_id",
                            "filename",
                            "mime_type",
                            "size",
                            "sha256",
                            "path",
                        )
                    }
                    for attachment in downloaded[:50]
                ],
                "snapshot_limits": {
                    "description_chars": 8_000,
                    "comments": 10,
                    "comment_chars": 2_000,
                    "attachments": 50,
                    "complete_comments": len(comments) <= 10,
                    "complete_attachments": len(downloaded) <= 50,
                },
                "evidence": {
                    "directory": str(issue_root),
                    "manifest": str(manifest_path),
                },
                "prepared_by": "jira-mcp",
                "prepared_at": manifest["materialized_at"],
            }
            return {
                "issue_key": key,
                "evidence_directory": str(issue_root),
                "manifest": str(manifest_path),
                "attachment_count": len(downloaded),
                "attachments": downloaded,
                "snapshot": snapshot,
            }

    async def get_attachment_excerpt(
        self,
        issue_key: str,
        attachment_id: str,
        *,
        max_bytes: int = 65_536,
    ) -> dict[str, Any]:
        key = validate_issue_key(issue_key)
        normalized_id = attachment_id.strip()
        bounded_max = max(1024, min(max_bytes, 262_144))
        attachments = await self._attachment_records(key)
        match = self._attachment(attachments, normalized_id)
        mime_type = str(match.get("mimeType", "application/octet-stream")).lower()
        if (
            not mime_type.startswith(_TEXT_MIME_PREFIXES)
            and mime_type not in _TEXT_MIME_TYPES
        ):
            raise ValueError("Jira attachment is not a supported text type")
        async with self._client.stream("GET", self._attachment_url(match)) as response:
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                remaining = bounded_max - len(chunks)
                if remaining <= 0:
                    break
                chunks.extend(chunk[:remaining])
        return {
            "attachment_id": normalized_id,
            "filename": str(match.get("filename", ""))[:1000],
            "mime_type": mime_type,
            "excerpt": bytes(chunks).decode("utf-8", errors="replace"),
            "truncated": int(match.get("size", 0) or 0) > len(chunks),
        }

    async def add_comment(
        self,
        issue_key: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self.settings.write_enabled:
            raise PermissionError("Jira writes are disabled")
        key = validate_issue_key(issue_key)
        normalized_body = body.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_body or len(normalized_body) > 20_000:
            raise ValueError("Jira comment must contain 1-20000 characters")
        if not normalized_key or len(normalized_key) > 128:
            raise ValueError("idempotency_key must contain 1-128 characters")
        body_hash = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
        marker_hash = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()[:20]
        marker = f"[knoa-operation:{marker_hash}]"
        lock = self._comment_locks.setdefault(normalized_key, asyncio.Lock())
        async with lock:
            action, created = self.store.begin_comment_action(
                normalized_key,
                key,
                body_hash,
                marker,
            )
            if action["state"] == "succeeded":
                return {
                    "status": "succeeded",
                    "comment_id": action["comment_id"],
                    "replayed": True,
                }
            existing = await self._find_comment_marker(key, marker)
            if existing:
                self.store.update_comment_action(
                    normalized_key,
                    "succeeded",
                    comment_id=existing,
                )
                return {
                    "status": "succeeded",
                    "comment_id": existing,
                    "replayed": True,
                }
            if not created:
                return {
                    "status": "outcome_unknown",
                    "retry_allowed": False,
                    "message": "Verify Jira before deciding whether to retry.",
                }
            rendered_body = f"{marker}\n{normalized_body}"
            payload_body: Any = rendered_body
            if self.settings.api_version == "3":
                payload_body = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": rendered_body}],
                        }
                    ],
                }
            try:
                response = await self._request(
                    "POST",
                    f"{self.api_root}/issue/{key}/comment",
                    json={"body": payload_body},
                )
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    self.store.update_comment_action(normalized_key, "failed")
                    raise
                self.store.update_comment_action(normalized_key, "outcome_unknown")
                return {"status": "outcome_unknown", "retry_allowed": False}
            except (httpx.TimeoutException, httpx.TransportError):
                self.store.update_comment_action(normalized_key, "outcome_unknown")
                return {"status": "outcome_unknown", "retry_allowed": False}
            comment_id = (
                str(response.get("id", "")) if isinstance(response, dict) else ""
            )
            self.store.update_comment_action(
                normalized_key,
                "succeeded",
                comment_id=comment_id,
            )
            return {
                "status": "succeeded",
                "comment_id": comment_id,
                "replayed": False,
            }

    async def _find_comment_marker(self, issue_key: str, marker: str) -> str:
        for comment in await self.get_comments(issue_key, limit=100):
            if marker in str(comment.get("body", "")):
                return str(comment.get("id", ""))
        return ""


def _named(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name", value.get("value", "")))
    return "" if value is None else str(value)


def _display_user(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("displayName")
        or value.get("emailAddress")
        or value.get("name")
        or value.get("accountId")
        or ""
    )


def _transition_allowed_value(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"id": "", "name": str(value)[:1000]}
    return {
        "id": str(
            value.get("id")
            or value.get("accountId")
            or value.get("name")
            or value.get("value")
            or ""
        ),
        "name": str(
            value.get("name")
            or value.get("displayName")
            or value.get("value")
            or ""
        )[:1000],
    }


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
