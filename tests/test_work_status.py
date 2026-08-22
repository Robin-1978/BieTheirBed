from knoa_platform.work_status import (
    product_task_work_status,
    task_work_status,
    turn_work_status,
)


def test_work_status_hides_task_implementation_states() -> None:
    assert task_work_status("queued").model_dump() == {
        "status": "queued",
        "terminal": False,
        "requires_user": False,
        "recoverable": False,
        "recommended_action": "wait",
    }
    assert task_work_status("waiting_approval", pending_approval_count=1).status == "waiting_for_you"
    assert task_work_status("paused").recommended_action == "resume"
    assert task_work_status("failed").recommended_action == "retry"


def test_turn_and_task_share_the_same_user_vocabulary() -> None:
    assert turn_work_status("running").status == "working"
    assert turn_work_status("completed").terminal is True
    assert turn_work_status("cancelled").status == "cancelled"


def test_product_task_keeps_definition_lifecycle_separate() -> None:
    assert product_task_work_status("paused", "running").status == "paused"
    assert product_task_work_status("active", "waiting_approval", pending_approval_count=1).status == "waiting_for_you"
    assert product_task_work_status("archived", "completed") is None
