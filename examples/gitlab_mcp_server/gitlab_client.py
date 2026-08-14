"""GitLab REST adapter and durable state for the reference MCP package."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
_ACTIVE_JOB_STATUSES = frozenset(
    {
        "created",
        "pending",
        "preparing",
        "running",
        "waiting_for_resource",
        "scheduled",
        "canceling",
    }
)
_RETRYABLE_JOB_STATUSES = frozenset({"failed", "canceled"})
_SNAPSHOT_TRACE_LINES = 120
_SNAPSHOT_TRACE_BYTES = 8 * 1024
_SNAPSHOT_MAX_FAILED_JOBS = 8
logger = logging.getLogger("gitlab-mcp-example")


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

    def record_observed_failure(self, source_id: str, event_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO observed_failures(
                       source_id, event_id, observed_at
                   ) VALUES (?, ?, ?)""",
                (source_id, event_id, time.time()),
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
        self._current_user: dict[str, Any] | None = None

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

    async def current_user(self) -> dict[str, Any]:
        if self._current_user is None:
            payload = await self._json("GET", "/api/v4/user")
            if not isinstance(payload, dict) or not payload.get("id"):
                raise TypeError("GitLab returned an invalid current user")
            self._current_user = payload
        return dict(self._current_user)

    async def pipeline_attribution(
        self,
        project: str,
        pipeline: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether a pipeline belongs to the authenticated contributor.

        A main-branch pipeline may be triggered by the merger, and a packaging
        pipeline may be triggered by ``ci-robot``.  The commit's associated MR
        author therefore takes precedence over the pipeline trigger user.
        """
        owner = await self.current_user()
        owner_id = str(owner.get("id", ""))
        owner_username = str(owner.get("username", "")).casefold()
        owner_emails = {
            str(owner.get(key, "")).casefold()
            for key in ("email", "public_email", "commit_email")
            if owner.get(key)
        }
        trigger = pipeline.get("user") if isinstance(pipeline.get("user"), dict) else {}
        trigger_matches = (
            str(trigger.get("id", "")) == owner_id
            or str(trigger.get("username", "")).casefold() == owner_username
        )
        sha = str(pipeline.get("sha", "")).strip()
        merge_requests: list[dict[str, Any]] = []
        if sha:
            payload = await self._json(
                "GET",
                f"/api/v4/projects/{self._project(project)}/repository/commits/{sha}/merge_requests",
            )
            if not isinstance(payload, list):
                raise TypeError("GitLab returned an invalid commit merge request list")
            merge_requests = [item for item in payload if isinstance(item, dict)]
        owned_merge_requests = [
            item for item in merge_requests
            if isinstance(item.get("author"), dict)
            and (
                str(item["author"].get("id", "")) == owner_id
                or str(item["author"].get("username", "")).casefold()
                == owner_username
            )
        ]
        commit_matches = False
        commit: dict[str, Any] = {}
        if sha and not trigger_matches and not owned_merge_requests:
            payload = await self._json(
                "GET",
                f"/api/v4/projects/{self._project(project)}/repository/commits/{sha}",
            )
            if not isinstance(payload, dict):
                raise TypeError("GitLab returned an invalid commit")
            commit = payload
            commit_matches = any(
                str(payload.get(key, "")).casefold() in owner_emails
                for key in ("author_email", "committer_email")
            )
        reasons = []
        if trigger_matches:
            reasons.append("pipeline_user")
        if owned_merge_requests:
            reasons.append("merge_request_author")
        if commit_matches:
            reasons.append("commit_email")
        return {
            "eligible": bool(reasons),
            "reasons": reasons,
            "owner": {
                "id": owner.get("id"),
                "username": owner.get("username"),
            },
            "pipeline_user": {
                "id": trigger.get("id"),
                "username": trigger.get("username"),
            },
            "merge_requests": [
                {
                    "iid": item.get("iid"),
                    "title": item.get("title"),
                    "source_branch": item.get("source_branch"),
                    "target_branch": item.get("target_branch"),
                    "author": item.get("author"),
                }
                for item in owned_merge_requests
            ],
            "commit": {
                key: commit.get(key)
                for key in ("id", "title", "author_email", "committer_email")
                if key in commit
            },
        }

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
        return tuple(
            self._job_summary(item) for item in payload if isinstance(item, dict)
        )

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
        buffer = bytearray()
        total = 0
        async with self._client.stream(
            "GET", path, headers={"Range": f"bytes=-{max_bytes}"}
        ) as response:
            response.raise_for_status()
            partial = response.status_code == 206
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    del buffer[: len(buffer) - max_bytes]
        lines = bytes(buffer).decode("utf-8", errors="replace").splitlines()
        selected = lines[-tail_lines:]
        return {
            "project": project,
            "job_id": job_id,
            "trace": "\n".join(selected),
            "tail_lines": len(selected),
            "truncated_by_lines": len(lines) > len(selected),
            "truncated_by_bytes": partial or total > max_bytes,
        }

    @staticmethod
    def _job_summary(item: dict[str, Any]) -> dict[str, Any]:
        pipeline = item.get("pipeline") if isinstance(item.get("pipeline"), dict) else {}
        runner = item.get("runner") if isinstance(item.get("runner"), dict) else {}
        return {
            key: item.get(key)
            for key in (
                "id",
                "name",
                "stage",
                "status",
                "failure_reason",
                "allow_failure",
                "created_at",
                "started_at",
                "finished_at",
                "duration",
                "queued_duration",
                "web_url",
            )
            if key in item
        } | {
            "pipeline_id": pipeline.get("id"),
            "runner": runner.get("description") or runner.get("name"),
        }

    @staticmethod
    def _pipeline_summary(item: dict[str, Any]) -> dict[str, Any]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        return {
            key: item.get(key)
            for key in (
                "id",
                "status",
                "ref",
                "sha",
                "source",
                "created_at",
                "updated_at",
                "web_url",
            )
            if key in item
        } | {
            "user": {
                "id": user.get("id"),
                "username": user.get("username"),
                "name": user.get("name"),
            }
        }

    @staticmethod
    def _latest_logical_jobs(
        attempts: tuple[dict[str, Any], ...]
    ) -> tuple[dict[str, Any], ...]:
        """Collapse retried Job attempts to the newest instance per Job name."""
        latest: dict[str, dict[str, Any]] = {}
        unnamed: list[dict[str, Any]] = []
        for job in attempts:
            name = str(job.get("name", "")).strip()
            if not name:
                unnamed.append(job)
                continue
            current = latest.get(name)
            if current is None or int(job.get("id", 0) or 0) > int(
                current.get("id", 0) or 0
            ):
                latest[name] = job
        return tuple(
            sorted(
                (*latest.values(), *unnamed),
                key=lambda job: int(job.get("id", 0) or 0),
            )
        )

    async def prepare_failure_snapshot(
        self,
        project: str,
        pipeline_id: str,
        *,
        pipeline: dict[str, Any] | None = None,
        attribution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect the bounded evidence an Agent needs for a failed pipeline.

        This is intentionally GitLab-specific.  The generic Platform only carries
        the resulting Resource/Task; it does not know how to diagnose CI failures.
        The snapshot is sufficient for an initial decision.  Retry still performs
        its own live checks in ``_retry``.
        """
        pipeline = pipeline or await self.get_pipeline(project, pipeline_id)
        attempts = await self.list_pipeline_jobs(project, pipeline_id)
        jobs = self._latest_logical_jobs(attempts)
        failed = [
            job for job in jobs
            if str(job.get("status", "")).casefold() in _RETRYABLE_JOB_STATUSES
        ]
        traces: list[dict[str, Any]] = []
        for job in failed[:_SNAPSHOT_MAX_FAILED_JOBS]:
            job_id = str(job.get("id", ""))
            if not _NUMERIC_ID.fullmatch(job_id):
                continue
            trace = await self.get_job_trace(
                project,
                job_id,
                tail_lines=_SNAPSHOT_TRACE_LINES,
                max_bytes=_SNAPSHOT_TRACE_BYTES,
            )
            trace_text = str(trace.get("trace", ""))
            traces.append({
                "job": job,
                "trace": trace,
                "failure_fingerprint": hashlib.sha256(
                    trace_text.encode("utf-8")
                ).hexdigest()[:20],
            })
        compile_jobs = [
            job for job in jobs
            if any(
                marker in str(job.get("name", "")).casefold()
                or marker in str(job.get("stage", "")).casefold()
                for marker in ("build", "compile")
            )
            and str(job.get("status", "")).casefold() != "manual"
        ]
        failed_compile = [
            job for job in compile_jobs
            if str(job.get("status", "")).casefold() in _RETRYABLE_JOB_STATUSES
        ]
        succeeded_compile = [
            job for job in compile_jobs
            if str(job.get("status", "")).casefold() == "success"
        ]
        oom_jobs = [
            item["job"] for item in traces
            if "killed (program cc1plus)" in str(item["trace"].get("trace", "")).casefold()
            or "out of memory" in str(item["trace"].get("trace", "")).casefold()
            or re.search(
                r"\boom\b",
                str(item["trace"].get("trace", "")),
                flags=re.IGNORECASE,
            )
        ]
        return {
            "pipeline": self._pipeline_summary(pipeline),
            "jobs": jobs,
            "failed_jobs": failed,
            "failed_job_traces": traces,
            "compile_summary": {
                "total": len(compile_jobs),
                "succeeded": len(succeeded_compile),
                "failed": len(failed_compile),
                "skipped": sum(
                    str(job.get("status", "")).casefold() == "skipped"
                    for job in compile_jobs
                ),
            },
            "signals": {
                "likely_oom": bool(oom_jobs and succeeded_compile),
                "oom_job_ids": [str(job.get("id")) for job in oom_jobs],
                "snapshot_complete": len(failed) <= _SNAPSHOT_MAX_FAILED_JOBS,
            },
            "prepared_by": "gitlab-mcp",
            "prepared_at": time.time(),
            "attribution": attribution or {},
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
            try:
                pipeline = await self.get_pipeline(
                    project, str(event_payload["pipeline_id"])
                )
                attribution = await self.pipeline_attribution(project, pipeline)
                if not attribution["eligible"]:
                    self.store.record_observed_failure(source_id, event_id)
                    continue
                snapshot = await self.prepare_failure_snapshot(
                    project,
                    str(event_payload["pipeline_id"]),
                    pipeline=pipeline,
                    attribution=attribution,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "GitLab failure snapshot preparation failed for %s pipeline %s",
                    project,
                    event_payload["pipeline_id"],
                )
                continue
            event_payload["snapshot"] = snapshot
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
            if kind == "job":
                job = await self.get_job(project, target_id)
                status = str(job.get("status", "")).strip().lower()
                if status in _ACTIVE_JOB_STATUSES:
                    failure = {
                        "error": (
                            f"GitLab job is already active ({status}); retry is blocked"
                        )
                    }
                    self.store.complete_retry(idempotency_key, "failed", failure)
                    raise RuntimeError(failure["error"])
                if status not in _RETRYABLE_JOB_STATUSES:
                    failure = {
                        "error": (
                            "GitLab job is not retryable from status "
                            f"{status or 'unknown'}"
                        )
                    }
                    self.store.complete_retry(idempotency_key, "failed", failure)
                    raise RuntimeError(failure["error"])
                pipeline = job.get("pipeline")
                pipeline_id = (
                    str(pipeline.get("id", ""))
                    if isinstance(pipeline, dict)
                    else ""
                )
                job_name = str(job.get("name", "")).strip()
                if not _NUMERIC_ID.fullmatch(pipeline_id) or not job_name:
                    failure = {
                        "error": (
                            "GitLab job lacks pipeline identity; active retry check "
                            "cannot be completed"
                        )
                    }
                    self.store.complete_retry(idempotency_key, "failed", failure)
                    raise RuntimeError(failure["error"])
                pipeline_jobs = await self.list_pipeline_jobs(project, pipeline_id)
                active_matches = [
                    candidate
                    for candidate in pipeline_jobs
                    if str(candidate.get("id", "")) != target_id
                    and str(candidate.get("name", "")).strip() == job_name
                    and str(candidate.get("status", "")).strip().lower()
                    in _ACTIVE_JOB_STATUSES
                ]
                if active_matches:
                    active = active_matches[0]
                    failure = {
                        "error": (
                            "A retry of this GitLab job is already active "
                            f"(job {active.get('id')}, status {active.get('status')}); "
                            "retry is blocked"
                        )
                    }
                    self.store.complete_retry(idempotency_key, "failed", failure)
                    raise RuntimeError(failure["error"])
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
