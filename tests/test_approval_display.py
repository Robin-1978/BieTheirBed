from knoa_platform.approvals.display import approval_display


def test_approval_display_adds_action_target_and_instruction() -> None:
    display = approval_display(
        "gitlab.retry_job",
        {"job_id": 42, "token": "secret"},
        "external_side_effect:medium",
        human_instruction="Retry the failed build job after checking it is not running.",
    )

    assert display["action_summary"] == "gitlab · retry job"
    assert display["target_summary"] == "job id: 42"
    assert display["instruction_excerpt"].startswith("Retry the failed build job")
    assert display["manual_reason"] == "policy_confirmation"
    assert "secret" not in display["arguments_preview"]


def test_approval_display_exposes_reviewer_advice_without_making_it_authority() -> None:
    display = approval_display(
        "write_file",
        {"path": "/tmp/report.md"},
        "local_write:high; reviewer[reviewer_agent/qwen3.5-4b]=deny: Target is outside the requested folder",
        human_instruction="Write the report in the workspace.",
    )

    assert display["reviewer_decision"] == "deny"
    assert display["reviewer_reason"] == "Target is outside the requested folder"
    assert display["reviewer_id"] == "reviewer_agent"
    assert display["reviewer_model"] == "qwen3.5-4b"
    assert display["manual_reason"] == "high_risk"
