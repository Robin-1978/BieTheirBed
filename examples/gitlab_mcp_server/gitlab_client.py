"""GitLab REST adapter and durable state for the reference MCP package."""
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
from urllib.parse import quote, urlsplit

import httpx

_NUMERIC_ID = re.compile(r"^[1-9][0-9]*$")


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
class GitLabSettings:
    base_url: str
    token: str
    projects: tuple[str, ...]
    poll_interval_seconds: int
    max_pipelines: int
    retention_days: int
    state_path: Path
    actions_enabled: bool

    @classmethod
    def from_env(cls) -> GitLabSettings:
        base_url = _required_env("GITLAB_URL").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("GITLAB_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("GITLAB_URL must not contain credentials or fragments")
        projects = tuple(
            item.strip()
            for item in _required_env("GITLAB_PROJECTS").split(",")
            if item.strip()
        )
        if not projects or len(projects) != len(set(projects)):
            raise ValueError("GITLAB_PROJECTS must contain unique project IDs or paths")
        state_path = Path(
            os.environ.get(
                "GITLAB_MCP_STATE_PATH", "~/.knoa/data/gitlab-mcp.db"
            )
        ).expanduser().resolve()
        return cls(
            base_url=base_url,
            token=_required_env("GITLAB_TOKEN"),
            projects=projects,
            poll_interval_seconds=_bounded_int(
                "GITLAB_POLL_INTERVAL_SECONDS", 60, 10, 3600
            ),
            max_pipelines=_bounded_int("GITLAB_MAX_PIPELINES", 50, 1, 100),
            retention_days=_bounded_int("GITLAB_EVENT_RETENTION_DAYS", 7, 1, 365),
            state_path=state_path,
            actions_enabled=os.environ.get("GITLAB_ACTIONS_ENABLED", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
        )


class GitLabStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS event_sources (
                    source_id TEXT PRIMARY KEY,
                    initialized_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observed_failures (
                    source_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    PRIMARY KEY(source_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS failure_events (
                    event_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    detected_at REAL NOT NULL,
                    retained_until REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retry_actions (
                    idempotency_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT NOT NULL,
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

    def source_initialized(self, source_id: str) -> bool:
        with self._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM event_sources WHERE source_id=?", (source_id,)
                ).fetchone()
                is not None
            )

    def initialize_source(self, source_id: str, event_ids: tuple[str, ...]) -> None:
        now = time.time()
        with self._connect() as db:
            db.executemany(
                """INSERT OR IGNORE INTO observed_failures(
                       source_id, event_id, observed_at
                   ) VALUES (?, ?, ?)""",
                ((source_id, event_id, now) for event_id in event_ids),
            )
            db.execute(
                "INSERT OR IGNORE INTO event_sources(source_id, initialized_at) VALUES (?, ?)",
                (source_id, now),
            )

    def failure_observed(self, source_id: str, event_id: str) -> bool:
        with self._connect() as db:
            return (
                db.execute(
                    """SELECT 1 FROM observed_failures
                       WHERE source_id=? AND event_id=?""",
                    (source_id, event_id),
                ).fetchone()
                is not None
            )

    def add_failure_event(
        self,
        source_id: str,
        event_id: str,
        payload: dict[str, Any],
        retention_seconds: float,
    ) -> bool:
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO failure_events(
                       event_id, payload_json, detected_at, retained_until
                   ) VALUES (?, ?, ?, ?)""",
                (
                    event_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                    now + retention_seconds,
                ),
            )
            db.execute(
                """INSERT OR IGNORE INTO observed_failures(
                       source_id, event_id, observed_at
                   ) VALUES (?, ?, ?)""",
                (source_id, event_id, now),
            )
            return cursor.rowcount == 1

    def list_failure_events(self) -> tuple[dict[str, Any], ...]:
        self._cleanup_events()
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, payload_json, detected_at
                   FROM failure_events ORDER BY detected_at, event_id"""
            ).fetchall()
        return tuple(
            {
                "event_id": row["event_id"],
                "payload": json.loads(row["payload_json"]),
                "detected_at": row["detected_at"],
            }
            for row in rows
        )

    def get_failure_event(self, event_id: str) -> dict[str, Any] | None:
        self._cleanup_events()
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json, detected_at FROM failure_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "event_id": event_id,
            "payload": json.loads(row["payload_json"]),
            "detected_at": row["detected_at"],
        }

    def _cleanup_events(self) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM failure_events WHERE retained_until < ?", (time.time(),)
            )

    def claim_retry(
        self, idempotency_key: str, request_hash: str
    ) -> tuple[str, dict[str, Any]]:
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM retry_actions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise ValueError("idempotency key conflicts with another retry")
                return row["state"], json.loads(row["result_json"])
            db.execute(
                """INSERT INTO retry_actions(
                       idempotency_key, request_hash, state, result_json, updated_at
                   ) VALUES (?, ?, 'pending', '{}', ?)""",
                (idempotency_key, request_hash, now),
            )
        return "new", {}

    def complete_retry(
        self, idempotency_key: str, state: str, result: dict[str, Any]
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE retry_actions SET state=?, result_json=?, updated_at=?
                   WHERE idempotency_key=?""",
                (
                    state,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    time.time(),
                    idempotency_key,
                ),
            )


class GitLabClient:
    def __init__(self, settings: GitLabSettings, store: GitLabStateStore) -> None:
        self.settings = settings
        self.store = store
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={"PRIVATE-TOKEN": settings.token, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )
        self._action_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self._client.aclose()

    def _project(self, project: str) -> str:
        if project not in self.settings.projects:
            raise ValueError("GitLab project is not configured")
        return quote(project, safe="")

    @staticmethod
    def _numeric_id(value: str, kind: str) -> str:
        normalized = value.strip()
        if not _NUMERIC_ID.fullmatch(normalized):
            raise ValueError(f"GitLab {kind} ID must be a positive integer")
        return normalized

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def get_pipeline(self, project: str, pipeline_id: str) -> dict[str, Any]:
        pipeline_id = self._numeric_id(pipeline_id, "pipeline")
        payload = await self._json(
            "GET", f"/api/v4/projects/{self._project(project)}/pipelines/{pipeline_id}"
        )
        if not isinstance(payload, dict):
            raise TypeError("GitLab returned an invalid pipeline")
        return payload

    async def list_pipeline_jobs(
        self, project: str, pipeline_id: str
    ) -> tuple[dict[str, Any], ...]:
        pipeline_id = self._numeric_id(pipeline_id, "pipeline")
        payload = await self._json(
            "GET",
            f"/api/v4/projects/{self._project(project)}/pipelines/{pipeline_id}/jobs",
            params={"per_page": 100, "include_retried": "true"},
        )
        if not isinstance(payload, list):
            raise TypeError("GitLab returned an invalid job list")
        return tuple(item for item in payload if isinstance(item, dict))

    async def get_job(self, project: str, job_id: str) -> dict[str, Any]:
        job_id = self._numeric_id(job_id, "job")
        payload = await self._json(
            "GET", f"/api/v4/projects/{self._project(project)}/jobs/{job_id}"
        )
        if not isinstance(payload, dict):
            raise TypeError("GitLab returned an invalid job")
        return payload

    async def get_job_trace(
        self,
        project: str,
        job_id: str,
        *,
        tail_lines: int = 400,
        max_bytes: int = 131_072,
    ) -> dict[str, Any]:
        job_id = self._numeric_id(job_id, "job")
        if not 1 <= tail_lines <= 2000:
            raise ValueError("tail_lines must be between 1 and 2000")
        if not 1024 <= max_bytes <= 1_048_576:
            raise ValueError("max_bytes must be between 1024 and 1048576")
        path = f"/api/v4/projects/{self._project(project)}/jobs/{job_id}/trace"
        chunks: list[bytes] = []
        total = 0
        async with self._client.stream(
            "GET", path, headers={"Range": f"bytes=-{max_bytes}"}
        ) as response:
            response.raise_for_status()
            partial = response.status_code == 206
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(
                        "GitLab did not honor the bounded trace range request"
                    )
                chunks.append(chunk)
        lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
        selected = lines[-tail_lines:]
        return {
            "project": project,
            "job_id": job_id,
            "trace": "\n".join(selected),
            "tail_lines": len(selected),
            "truncated_by_lines": len(lines) > len(selected),
            "truncated_by_bytes": partial,
        }

    async def poll_failure_events(self) -> tuple[dict[str, Any], ...]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        for project in self.settings.projects:
            payload = await self._json(
                "GET",
                f"/api/v4/projects/{self._project(project)}/pipelines",
                params={"status": "failed", "per_page": self.settings.max_pipelines},
            )
            if not isinstance(payload, list):
                raise TypeError("GitLab returned an invalid pipeline list")
            for pipeline in payload:
                if not isinstance(pipeline, dict) or "id" not in pipeline:
                    continue
                event_payload = {
                    "project": project,
                    "pipeline_id": str(pipeline["id"]),
                    "status": str(pipeline.get("status", "failed")),
                    "sha": str(pipeline.get("sha", "")),
                    "ref": str(pipeline.get("ref", "")),
                    "updated_at": str(pipeline.get("updated_at", "")),
                    "web_url": str(pipeline.get("web_url", "")),
                }
                identity = json.dumps(event_payload, sort_keys=True)
                event_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
                candidates.append((event_id, event_payload))
        source_identity = json.dumps(
            {
                "base_url": self.settings.base_url,
                "projects": self.settings.projects,
                "max_pipelines": self.settings.max_pipelines,
            },
            sort_keys=True,
        )
        source_id = hashlib.sha256(source_identity.encode()).hexdigest()[:32]
        if not self.store.source_initialized(source_id):
            self.store.initialize_source(
                source_id, tuple(event_id for event_id, _ in candidates)
            )
            return ()
        created: list[dict[str, Any]] = []
        retention = self.settings.retention_days * 24 * 60 * 60
        for event_id, payload in candidates:
            if self.store.failure_observed(source_id, event_id):
                continue
            if self.store.add_failure_event(
                source_id, event_id, payload, retention
            ):
                created.append({"event_id": event_id, **payload})
        return tuple(created)

    async def retry_pipeline(
        self, project: str, pipeline_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        return await self._retry("pipeline", project, pipeline_id, idempotency_key)

    async def retry_job(
        self, project: str, job_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        return await self._retry("job", project, job_id, idempotency_key)

    async def _retry(
        self, kind: str, project: str, target_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        if not self.settings.actions_enabled:
            raise PermissionError("GitLab retry actions are disabled")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key must contain 1-128 characters")
        encoded_project = self._project(project)
        target_id = self._numeric_id(target_id, kind)
        request = {
            "kind": kind,
            "project": project,
            "target_id": target_id,
        }
        request_hash = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        lock = self._action_locks.setdefault(idempotency_key, asyncio.Lock())
        async with lock:
            state, result = self.store.claim_retry(idempotency_key, request_hash)
            if state == "success":
                return result
            if state in {"failed", "outcome_unknown", "pending"}:
                raise RuntimeError(result.get("error", f"retry action is {state}"))
            path = (
                f"/api/v4/projects/{encoded_project}/pipelines/{target_id}/retry"
                if kind == "pipeline"
                else f"/api/v4/projects/{encoded_project}/jobs/{target_id}/retry"
            )
            try:
                payload = await self._json("POST", path)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                failure = {"error": f"retry outcome is unknown: {type(exc).__name__}"}
                self.store.complete_retry(idempotency_key, "outcome_unknown", failure)
                raise RuntimeError(failure["error"]) from exc
            except Exception as exc:
                failure = {"error": str(exc)[:1000]}
                self.store.complete_retry(idempotency_key, "failed", failure)
                raise
            result = {
                "status": "success",
                "kind": kind,
                "project": project,
                "target_id": target_id,
                "provider_result": payload,
            }
            self.store.complete_retry(idempotency_key, "success", result)
            return result
