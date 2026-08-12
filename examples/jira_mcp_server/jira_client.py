"""Jira REST adapter and local durable state for the MCP example server."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*$")
_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
    }
)


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
        api_version = os.environ.get("JIRA_API_VERSION", "2").strip()
        if api_version not in {"2", "3"}:
            raise ValueError("JIRA_API_VERSION must be 2 or 3")
        state_path = (
            Path(
                os.environ.get(
                    "JIRA_MCP_STATE_PATH",
                    "~/.pc-assistant/data/jira-mcp-example.db",
                )
            )
            .expanduser()
            .resolve()
        )
        return cls(
            base_url=base_url,
            username=_required_env("JIRA_USERNAME"),
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
            write_enabled=os.environ.get("JIRA_WRITE_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )


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
                    detected_at REAL NOT NULL,
                    retained_until REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS assignment_events_by_retention
                    ON assignment_events(retained_until, event_id);
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
    ) -> bool:
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO assignment_events(
                       event_id, issue_key, detected_at, retained_until
                   ) VALUES (?, ?, ?, ?)""",
                (event_id, issue_key, now, now + retention_seconds),
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
                """SELECT event_id, issue_key, detected_at
                   FROM assignment_events
                   ORDER BY detected_at, event_id"""
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_assignment_event(self, event_id: str) -> dict[str, Any] | None:
        self.cleanup_events()
        with self._connect() as db:
            row = db.execute(
                """SELECT event_id, issue_key, detected_at
                   FROM assignment_events WHERE event_id=?""",
                (event_id,),
            ).fetchone()
        return None if row is None else dict(row)

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
        created: list[dict[str, str]] = []
        retention = self.settings.retention_days * 24 * 60 * 60
        for issue in await self.search_assigned_issues():
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
                identity = (
                    f"initial:{issue.get('id', issue_key)}:{fields.get('created', '')}"
                )
            event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            if self.store.add_assignment_event(
                event_id,
                issue_key,
                retention_seconds=retention,
            ):
                created.append({"event_id": event_id, "issue_key": issue_key})
        return tuple(created)

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

    async def list_attachments(self, issue_key: str) -> tuple[dict[str, Any], ...]:
        key = validate_issue_key(issue_key)
        issue = await self._request(
            "GET",
            f"{self.api_root}/issue/{key}",
            params={"fields": "attachment"},
        )
        fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
        attachments = fields.get("attachment", []) if isinstance(fields, dict) else []
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

    async def get_attachment_excerpt(
        self,
        issue_key: str,
        attachment_id: str,
        *,
        max_bytes: int = 65_536,
    ) -> dict[str, Any]:
        key = validate_issue_key(issue_key)
        normalized_id = attachment_id.strip()
        if not _ATTACHMENT_ID.fullmatch(normalized_id):
            raise ValueError("Invalid Jira attachment ID")
        bounded_max = max(1024, min(max_bytes, 262_144))
        issue = await self._request(
            "GET",
            f"{self.api_root}/issue/{key}",
            params={"fields": "attachment"},
        )
        fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
        attachments = fields.get("attachment", []) if isinstance(fields, dict) else []
        match = next(
            (
                item
                for item in attachments
                if isinstance(item, dict) and str(item.get("id", "")) == normalized_id
            ),
            None,
        )
        if match is None:
            raise LookupError("Jira attachment does not belong to the issue")
        mime_type = str(match.get("mimeType", "application/octet-stream")).lower()
        if (
            not mime_type.startswith(_TEXT_MIME_PREFIXES)
            and mime_type not in _TEXT_MIME_TYPES
        ):
            raise ValueError("Jira attachment is not a supported text type")
        content_url = str(match.get("content", ""))
        target = urlsplit(urljoin(f"{self.settings.base_url}/", content_url))
        base = urlsplit(self.settings.base_url)
        if (
            target.username
            or target.password
            or target.scheme != base.scheme
            or target.hostname != base.hostname
            or target.port != base.port
        ):
            raise ValueError(
                "Jira attachment URL is outside the configured Jira origin"
            )
        async with self._client.stream("GET", target.geturl()) as response:
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


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
