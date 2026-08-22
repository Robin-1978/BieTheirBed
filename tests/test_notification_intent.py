from knoa_platform.notification_intent import notification_intent_for_event


def test_notification_intent_unifies_approval_and_interaction() -> None:
    approval = notification_intent_for_event("approval_requested")
    interaction = notification_intent_for_event("interaction_requested")
    assert approval is not None and approval.kind == "decision"
    assert interaction is not None and interaction.kind == "decision"
    assert approval.policy_key == interaction.policy_key == "waiting_approval"


def test_notification_intent_separates_result_and_recovery() -> None:
    assert notification_intent_for_event("completed").kind == "result"
    assert notification_intent_for_event("failed").kind == "recovery"
    assert notification_intent_for_event("tool_result") is None
