"""Persistent evidence, offline replay, approval, canary and rollback control."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from knoa_platform.sqlite_connection import connect_sqlite, initialize_wal


Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
CandidateState = Literal["draft", "evaluated", "awaiting_approval", "canary", "promoted", "rejected", "rolled_back"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImprovementEvidence(_Model):
    evidence_id: Identifier
    principal_id: str
    kind: Literal["explicit_feedback", "failed_execution", "recovery_result", "metric_regression"]
    subject_ref: str = Field(max_length=256)
    summary: str = Field(min_length=1, max_length=4000)
    created_at: float


class EvaluationCase(_Model):
    case_id: Identifier
    principal_id: str
    sanitized_input: str = Field(min_length=1, max_length=100_000)
    expected_invariants: tuple[str, ...] = Field(min_length=1, max_length=100)
    fixture_results: dict[str, Any] = Field(default_factory=dict)
    dataset_version: str = Field(min_length=1, max_length=128)
    created_at: float


class ImprovementCandidate(_Model):
    candidate_id: Identifier
    principal_id: str
    kind: Literal["prompt", "skill"]
    target_ref: str = Field(min_length=1, max_length=256)
    base_version: str = Field(min_length=1, max_length=128)
    proposed_version: str = Field(min_length=1, max_length=128)
    proposed_content: str = Field(min_length=1, max_length=500_000)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    state: CandidateState
    author: str = Field(min_length=1, max_length=256)
    created_at: float
    updated_at: float


class ReplayResult(_Model):
    replay_id: Identifier
    candidate_id: Identifier
    dataset_version: str
    case_count: int
    passed: bool
    quality_score: float = Field(ge=0, le=1)
    safety_violations: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    recovery_success_rate: float = Field(ge=0, le=1)
    details: tuple[dict[str, Any], ...]
    created_at: float


ReplayEvaluator = Callable[[ImprovementCandidate, EvaluationCase], dict[str, Any]]


class ImprovementService:
    """Evolution API is structurally incapable of changing security boundaries."""

    def __init__(self, database: str | Path, *, clock=time.time) -> None:
        self._path = Path(database)
        self._clock = clock
        initialize_wal(self._path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS improvement_evidence(
                    evidence_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    value_json TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_cases(
                    case_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    dataset_version TEXT NOT NULL, value_json TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_candidates(
                    candidate_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    state TEXT NOT NULL, value_json TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_replays(
                    replay_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL, value_json TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS improvement_promotions(
                    promotion_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL, scope_json TEXT NOT NULL,
                    state TEXT NOT NULL, approved_by TEXT NOT NULL,
                    rollback_target TEXT NOT NULL, metrics_json TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self._path, foreign_keys=True)

    @staticmethod
    def _id(prefix: str, value: Any) -> str:
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return f"{prefix}:{digest}"

    def record_evidence(self, principal_id: str, *, kind: str, subject_ref: str, summary: str) -> ImprovementEvidence:
        now = self._clock()
        value = ImprovementEvidence(
            evidence_id=self._id("evidence", [principal_id, kind, subject_ref, summary, time.time_ns()]),
            principal_id=principal_id, kind=kind, subject_ref=subject_ref, summary=summary, created_at=now,
        )
        with self._connect() as db:
            db.execute("INSERT INTO improvement_evidence VALUES(?,?,?,?)", (value.evidence_id, principal_id, value.model_dump_json(), now))
        return value

    def add_case(
        self,
        principal_id: str,
        *,
        sanitized_input: str,
        expected_invariants: tuple[str, ...],
        fixture_results: dict[str, Any] | None = None,
        dataset_version: str,
    ) -> EvaluationCase:
        # Cases accept explicitly sanitized text only; no Conversation/Task importer exists.
        now = self._clock()
        value = EvaluationCase(
            case_id=self._id("case", [principal_id, dataset_version, sanitized_input, expected_invariants]),
            principal_id=principal_id, sanitized_input=sanitized_input,
            expected_invariants=expected_invariants, fixture_results=fixture_results or {},
            dataset_version=dataset_version, created_at=now,
        )
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO improvement_cases VALUES(?,?,?,?,?)", (value.case_id, principal_id, dataset_version, value.model_dump_json(), now))
        return value

    def create_candidate(
        self,
        principal_id: str,
        *,
        kind: Literal["prompt", "skill"],
        target_ref: str,
        base_version: str,
        proposed_version: str,
        proposed_content: str,
        rationale: str,
        evidence_ids: tuple[str, ...],
        author: str,
    ) -> ImprovementCandidate:
        # No arbitrary patch/document field is accepted, so credentials, policy,
        # sandbox, trust roots and deployment identity cannot enter this API.
        if kind not in {"prompt", "skill"}:
            raise ValueError("Only Prompt and Skill candidates are supported")
        with self._connect() as db:
            found = db.execute(
                f"SELECT COUNT(*) FROM improvement_evidence WHERE principal_id=? AND evidence_id IN ({','.join('?' for _ in evidence_ids)})",
                (principal_id, *evidence_ids),
            ).fetchone()[0] if evidence_ids else 0
        if found != len(set(evidence_ids)):
            raise LookupError("Candidate evidence is missing or foreign")
        now = self._clock()
        value = ImprovementCandidate(
            candidate_id=self._id("candidate", [principal_id, kind, target_ref, base_version, proposed_version, proposed_content]),
            principal_id=principal_id, kind=kind, target_ref=target_ref,
            base_version=base_version, proposed_version=proposed_version,
            proposed_content=proposed_content, rationale=rationale,
            evidence_ids=evidence_ids, state="draft", author=author,
            created_at=now, updated_at=now,
        )
        self._put_candidate(value)
        return value

    def _put_candidate(self, value: ImprovementCandidate) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO improvement_candidates VALUES(?,?,?,?,?)
                   ON CONFLICT(candidate_id) DO UPDATE SET state=excluded.state,
                     value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (value.candidate_id, value.principal_id, value.state, value.model_dump_json(), value.updated_at),
            )

    def candidate(self, principal_id: str, candidate_id: str) -> ImprovementCandidate:
        with self._connect() as db:
            row = db.execute("SELECT value_json FROM improvement_candidates WHERE principal_id=? AND candidate_id=?", (principal_id, candidate_id)).fetchone()
        if row is None:
            raise LookupError("Improvement candidate not found")
        return ImprovementCandidate.model_validate_json(str(row["value_json"]))

    def list_candidates(self, principal_id: str) -> tuple[ImprovementCandidate, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT value_json FROM improvement_candidates WHERE principal_id=? ORDER BY updated_at DESC", (principal_id,)).fetchall()
        return tuple(ImprovementCandidate.model_validate_json(str(row["value_json"])) for row in rows)

    def replay(
        self,
        principal_id: str,
        candidate_id: str,
        *,
        dataset_version: str,
        evaluator: ReplayEvaluator | None = None,
    ) -> ReplayResult:
        candidate = self.candidate(principal_id, candidate_id)
        if candidate.state not in {"draft", "evaluated", "awaiting_approval"}:
            raise ValueError("Candidate cannot be replayed in its current state")
        with self._connect() as db:
            rows = db.execute("SELECT value_json FROM improvement_cases WHERE principal_id=? AND dataset_version=? ORDER BY case_id", (principal_id, dataset_version)).fetchall()
        cases = tuple(EvaluationCase.model_validate_json(str(row["value_json"])) for row in rows)
        if not cases:
            raise ValueError("Offline replay dataset is empty")
        run = evaluator or self._static_evaluator
        details = tuple(run(candidate, case) for case in cases)
        safety = sum(int(item.get("safety_violations", 0)) for item in details)
        quality = sum(float(item.get("quality_score", 0)) for item in details) / len(details)
        recovery = sum(float(item.get("recovery_success_rate", 0)) for item in details) / len(details)
        now = self._clock()
        value = ReplayResult(
            replay_id=self._id("replay", [candidate_id, dataset_version, time.time_ns()]),
            candidate_id=candidate_id, dataset_version=dataset_version, case_count=len(cases),
            passed=safety == 0 and quality >= 0.8 and recovery >= 0.8,
            quality_score=quality, safety_violations=safety,
            estimated_cost=sum(float(item.get("estimated_cost", 0)) for item in details),
            latency_ms=sum(float(item.get("latency_ms", 0)) for item in details),
            recovery_success_rate=recovery, details=details, created_at=now,
        )
        with self._connect() as db:
            db.execute("INSERT INTO improvement_replays VALUES(?,?,?,?,?)", (value.replay_id, principal_id, candidate_id, value.model_dump_json(), now))
        next_state: CandidateState = "awaiting_approval" if value.passed else "evaluated"
        self._put_candidate(candidate.model_copy(update={"state": next_state, "updated_at": now}))
        return value

    @staticmethod
    def _static_evaluator(candidate: ImprovementCandidate, case: EvaluationCase) -> dict[str, Any]:
        text = candidate.proposed_content.casefold()
        matched = sum(1 for invariant in case.expected_invariants if invariant.casefold() in text)
        quality = matched / len(case.expected_invariants)
        return {
            "case_id": case.case_id, "quality_score": quality,
            "safety_violations": 0, "estimated_cost": 0,
            "latency_ms": 0, "recovery_success_rate": quality,
            "network_writes": 0,
        }

    def approve(self, principal_id: str, candidate_id: str, *, approved_by: str, canary_scope: tuple[str, ...]) -> dict[str, Any]:
        candidate = self.candidate(principal_id, candidate_id)
        if candidate.state != "awaiting_approval":
            raise ValueError("Candidate has not passed replay")
        scope = tuple(sorted(set(value.strip() for value in canary_scope if value.strip())))
        if not scope or any(value in {"*", "all", "global"} for value in scope):
            raise ValueError("Canary scope must explicitly name subsequent work or templates")
        now = self._clock()
        promotion_id = self._id("promotion", [candidate_id, approved_by, scope, time.time_ns()])
        with self._connect() as db:
            db.execute("INSERT INTO improvement_promotions VALUES(?,?,?,?,?,?,?,?,?,?)", (
                promotion_id, principal_id, candidate_id, json.dumps(scope), "canary",
                approved_by, candidate.base_version, "{}", now, now,
            ))
        updated = candidate.model_copy(update={"state": "canary", "updated_at": now})
        self._put_candidate(updated)
        return {"promotion_id": promotion_id, "candidate_id": candidate_id, "state": "canary", "scope": scope, "rollback_target": candidate.base_version}

    def finish_canary(self, principal_id: str, candidate_id: str, *, promote: bool, metrics: dict[str, float]) -> ImprovementCandidate:
        candidate = self.candidate(principal_id, candidate_id)
        if candidate.state != "canary":
            raise ValueError("Candidate is not in canary")
        safety = float(metrics.get("safety_violations", 0))
        regression = float(metrics.get("quality_regression", 0))
        success = promote and safety <= 0 and regression <= 0
        state: CandidateState = "promoted" if success else "rolled_back"
        now = self._clock()
        with self._connect() as db:
            db.execute(
                """UPDATE improvement_promotions SET state=?, metrics_json=?, updated_at=?
                   WHERE principal_id=? AND candidate_id=? AND state='canary'""",
                (state, json.dumps(metrics, sort_keys=True), now, principal_id, candidate_id),
            )
        updated = candidate.model_copy(update={"state": state, "updated_at": now})
        self._put_candidate(updated)
        return updated

    def rollback(self, principal_id: str, candidate_id: str) -> ImprovementCandidate:
        candidate = self.candidate(principal_id, candidate_id)
        if candidate.state not in {"canary", "promoted"}:
            raise ValueError("Candidate has no active promotion")
        return self.finish_canary(principal_id, candidate_id, promote=False, metrics={}) if candidate.state == "canary" else self._rollback_promoted(candidate)

    def _rollback_promoted(self, candidate: ImprovementCandidate) -> ImprovementCandidate:
        now = self._clock()
        with self._connect() as db:
            db.execute("UPDATE improvement_promotions SET state='rolled_back', updated_at=? WHERE principal_id=? AND candidate_id=?", (now, candidate.principal_id, candidate.candidate_id))
        updated = candidate.model_copy(update={"state": "rolled_back", "updated_at": now})
        self._put_candidate(updated)
        return updated
