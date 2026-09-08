"""Tests for the universal Knoa Action Card data protocol and JSON schema."""

from __future__ import annotations

import json

from knoa_platform.action_card import (
    ACTION_CARD_JSON_SCHEMA,
    ActionCard,
    ActionCardBlock,
    ActionCardBuilder,
    ActionCardButton,
    ActionCardInput,
    ActionCardLevel,
    ActionCardSource,
    ActionCardStatus,
    KeyValueItem,
    validate_action_card,
)


def test_action_card_minimal_creation_and_dict() -> None:
    card = ActionCard(title="System Alert")
    data = card.to_dict()

    assert data["schema_version"] == "1.0"
    assert data["card_id"].startswith("card_")
    assert data["title"] == "System Alert"
    assert data["level"] == "info"
    assert data["status"] == "pending"
    assert data["source"]["plugin_name"] == "system"
    assert data["blocks"] == []
    assert data["inputs"] == []
    assert data["actions"] == []

    is_valid, err = validate_action_card(data)
    assert is_valid is True, err


def test_action_card_builder_full_suite() -> None:
    builder = (
        ActionCardBuilder(
            title="Motor Feedback Crash Diagnosed",
            card_id="card_diag_12345",
            level=ActionCardLevel.CRITICAL,
        )
        .set_subtitle("Affecting Robot SN: TBPR123456")
        .set_status(ActionCardStatus.PENDING)
        .set_source(
            plugin_name="jira",
            agent_name="coder",
            session_id="turn_abc987",
            expires_in_seconds=3600.0,
        )
        .add_callout(
            "Fatal exception detected in motion_controller.cc:88",
            level="error",
        )
        .add_markdown(
            "### 现场日志分析报告\n"
            "- **错误类型**: `SIGSEGV` / `Motor feedback timeout`\n"
            "- **触发模块**: Navigation PNC Subsystem\n"
        )
        .add_key_value(
            [
                KeyValueItem(key="工单号", value="TESTISSUE-125380"),
                {"key": "责任人", "value": "robotdev@gs-robot.com", "style": "bold"},
                {"key": "上位机固件", "value": "v2.6.4-rc1", "style": "code"},
            ]
        )
        .add_code_diff(
            filename="src/navigation/motion_controller.cc",
            language="cpp",
            unified_diff=(
                "--- a/src/navigation/motion_controller.cc\n"
                "+++ b/src/navigation/motion_controller.cc\n"
                "@@ -87,2 +87,5 @@\n"
                "+    if (!feedback) {\n"
                "+        LOG(ERROR) << \"Null motor feedback\";\n"
                "+        return false;\n"
                "+    }\n"
            ),
        )
        .add_artifact_link(
            name="robot_logs_evidence.tar.gz",
            url="https://gs-public-shared.oss-cn-shanghai.aliyuncs.com/logs/robot_logs.tar.gz",
            size_bytes=1048576,
            mime_type="application/gzip",
        )
        .add_input(
            id="resolution_note",
            label="补充解决说明（可选）",
            input_type="textarea",
            placeholder="输入针对此工单的处置意见...",
            required=False,
        )
        .add_input(
            id="notify_field",
            label="通知现场服务群",
            input_type="switch",
            default_value=True,
        )
        .add_action_button(
            id="btn_writeback_comment",
            label="确认回写诊断并流转",
            style="primary",
            action_type="invoke_tool",
            tool_name="jira.add_comment",
            arguments={
                "issue_key": "TESTISSUE-125380",
                "template": "three_section_diagnostic",
            },
            confirm_dialog={
                "title": "回写确认",
                "message": "确定将三段式诊断结论写回 TESTISSUE-125380 吗？",
            },
            include_form_inputs=True,
        )
        .add_action_button(
            id="btn_dismiss",
            label="忽略此卡片",
            style="outline",
            action_type="dismiss",
        )
        .set_metadata("jira_project", "TESTISSUE")
    )

    card = builder.build()
    data = card.to_dict()

    # Validate against strict JSON schema
    is_valid, err = validate_action_card(data)
    assert is_valid is True, err

    assert data["card_id"] == "card_diag_12345"
    assert data["title"] == "Motor Feedback Crash Diagnosed"
    assert data["subtitle"] == "Affecting Robot SN: TBPR123456"
    assert data["level"] == "critical"
    assert data["status"] == "pending"
    assert data["source"]["plugin_name"] == "jira"
    assert data["source"]["agent_name"] == "coder"
    assert data["source"]["session_id"] == "turn_abc987"
    assert data["source"]["expires_at"] is not None
    assert len(data["blocks"]) == 5
    assert len(data["inputs"]) == 2
    assert len(data["actions"]) == 2
    assert data["metadata"]["jira_project"] == "TESTISSUE"

    # Test JSON serialization / deserialization roundtrip
    json_str = card.to_json()
    reconstructed = ActionCard.from_json(json_str)

    assert reconstructed.card_id == card.card_id
    assert reconstructed.title == card.title
    assert reconstructed.level == ActionCardLevel.CRITICAL
    assert reconstructed.status == ActionCardStatus.PENDING
    assert reconstructed.source.plugin_name == "jira"
    assert len(reconstructed.blocks) == 5
    assert len(reconstructed.inputs) == 2
    assert len(reconstructed.actions) == 2
    assert reconstructed.actions[0].tool_name == "jira.add_comment"
    assert reconstructed.actions[0].confirm_dialog == {
        "title": "回写确认",
        "message": "确定将三段式诊断结论写回 TESTISSUE-125380 吗？",
    }


def test_schema_rejection_of_invalid_cards() -> None:
    # 1. Missing required field 'schema_version'
    bad_data: dict = {
        "card_id": "card_test",
        "title": "Bad Card",
        "level": "info",
        "status": "pending",
        "source": {"plugin_name": "test", "created_at": 123456.0},
    }
    is_valid, err = validate_action_card(bad_data)
    assert is_valid is False
    assert "'schema_version' is a required property" in str(err)

    # 2. Invalid level
    bad_level = dict(bad_data)
    bad_level["schema_version"] = "1.0"
    bad_level["level"] = "ultra_mega_urgent"
    is_valid, err = validate_action_card(bad_level)
    assert is_valid is False
    assert "'ultra_mega_urgent' is not one of" in str(err)

    # 3. Invalid button style
    bad_button = dict(bad_data)
    bad_button["schema_version"] = "1.0"
    bad_button["level"] = "info"
    bad_button["actions"] = [
        {
            "id": "act1",
            "label": "Click",
            "style": "neon_green",
            "action_type": "dismiss",
        }
    ]
    is_valid, err = validate_action_card(bad_button)
    assert is_valid is False
    assert "'neon_green' is not one of" in str(err)
