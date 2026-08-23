from pathlib import Path

import pytest

from knoa_platform.improvement import ImprovementService


def test_prompt_candidate_requires_evidence_replay_human_canary_and_explicit_promotion(
    tmp_path: Path,
) -> None:
    service = ImprovementService(tmp_path / "governance.db", clock=lambda: 1000.0)
    evidence = service.record_evidence(
        "principal-a", kind="explicit_feedback", subject_ref="task-a",
        summary="Always include a rollback section",
    )
    service.add_case(
        "principal-a", sanitized_input="deploy a service",
        expected_invariants=("rollback", "verify"), dataset_version="release-v1",
    )
    candidate = service.create_candidate(
        "principal-a", kind="prompt", target_ref="agent.release",
        base_version="1", proposed_version="2",
        proposed_content="Plan, verify, and provide rollback.",
        rationale="Improve safe delivery", evidence_ids=(evidence.evidence_id,),
        author="device-a",
    )
    replay = service.replay("principal-a", candidate.candidate_id, dataset_version="release-v1")
    assert replay.passed is True
    assert service.candidate("principal-a", candidate.candidate_id).state == "awaiting_approval"
    with pytest.raises(ValueError):
        service.approve("principal-a", candidate.candidate_id, approved_by="device-a", canary_scope=("global",))
    promotion = service.approve(
        "principal-a", candidate.candidate_id, approved_by="device-a",
        canary_scope=("task-template:release",),
    )
    assert promotion["state"] == "canary"
    promoted = service.finish_canary(
        "principal-a", candidate.candidate_id, promote=True,
        metrics={"safety_violations": 0, "quality_regression": 0},
    )
    assert promoted.state == "promoted"
    assert service.rollback("principal-a", candidate.candidate_id).state == "rolled_back"


def test_evolution_api_cannot_create_security_boundary_candidates(tmp_path: Path) -> None:
    service = ImprovementService(tmp_path / "governance.db")
    evidence = service.record_evidence(
        "principal-a", kind="failed_execution", subject_ref="task-a", summary="failed",
    )
    with pytest.raises(ValueError, match="Prompt and Skill"):
        service.create_candidate(
            "principal-a", kind="approval_policy",  # type: ignore[arg-type]
            target_ref="security.approval", base_version="1", proposed_version="2",
            proposed_content="allow all", rationale="faster",
            evidence_ids=(evidence.evidence_id,), author="device-a",
        )
    with pytest.raises(LookupError):
        service.create_candidate(
            "principal-b", kind="skill", target_ref="skill-a",
            base_version="1", proposed_version="2", proposed_content="safe",
            rationale="change", evidence_ids=(evidence.evidence_id,), author="device-b",
        )


def test_canary_regression_forces_rollback(tmp_path: Path) -> None:
    service = ImprovementService(tmp_path / "governance.db")
    evidence = service.record_evidence("p", kind="metric_regression", subject_ref="x", summary="x")
    service.add_case("p", sanitized_input="x", expected_invariants=("safe",), dataset_version="v1")
    candidate = service.create_candidate(
        "p", kind="skill", target_ref="skill-a", base_version="1", proposed_version="2",
        proposed_content="safe", rationale="x", evidence_ids=(evidence.evidence_id,), author="d",
    )
    service.replay("p", candidate.candidate_id, dataset_version="v1")
    service.approve("p", candidate.candidate_id, approved_by="d", canary_scope=("next-task:x",))
    result = service.finish_canary(
        "p", candidate.candidate_id, promote=True,
        metrics={"safety_violations": 1, "quality_regression": 0},
    )
    assert result.state == "rolled_back"
