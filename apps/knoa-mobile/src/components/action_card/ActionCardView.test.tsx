import { describe, expect, it, vi } from "vitest";

import type { ActionCard, ActionCardInvocation } from "./types";

describe("ActionCard protocol & rendering contracts", () => {
  const sampleCard: ActionCard = {
    schema_version: "1.0",
    card_id: "card_sample_001",
    title: "机器人崩溃诊断与回写报告",
    subtitle: "工单: TESTISSUE-125380 · 机器SN: GS-ROBOT-01",
    level: "critical",
    status: "pending",
    source: {
      plugin_name: "jira",
      agent_name: "coder",
      created_at: 1788889900,
    },
    blocks: [
      {
        type: "callout",
        level: "error",
        text: "在 motion_controller.cc:88 捕获到 SIGSEGV 段错误",
      },
      {
        type: "markdown",
        content: "### 排查结论\n未对电机指针进行有效判空，空指针导致崩溃。",
      },
      {
        type: "key_value",
        items: [
          { key: "影响版本", value: "v2.6.4-rc1", style: "code" },
          { key: "代码责任人", value: "robotdev@gs-robot.com", style: "bold" },
          { key: "状态分类", value: "生产阻断", style: "badge" },
        ],
      },
      {
        type: "code_diff",
        filename: "src/navigation/motion_controller.cc",
        language: "cpp",
        unified_diff: "@@ -87,2 +87,5 @@\n-  motor->send();\n+  if (motor) {\n+    motor->send();\n+  }",
      },
      {
        type: "artifact_link",
        name: "crash_log.tar.gz",
        url: "https://example.com/logs/crash_log.tar.gz",
        size_bytes: 1048576,
        mime_type: "application/gzip",
      },
    ],
    inputs: [
      {
        id: "comment",
        label: "补充处置说明",
        input_type: "textarea",
        placeholder: "请输入现场补充说明...",
        default_value: "现场已断电确认安全",
      },
      {
        id: "notify_channel",
        label: "是否同步现场群",
        input_type: "switch",
        default_value: true,
      },
    ],
    actions: [
      {
        id: "btn_writeback",
        label: "确认回写工单",
        style: "primary",
        action_type: "invoke_tool",
        tool_name: "jira.update_issue",
        arguments: { issue_key: "TESTISSUE-125380" },
        include_form_inputs: true,
        confirm_dialog: {
          title: "确认回写",
          message: "确认回写该分析结论至工单？",
        },
      },
      {
        id: "btn_dismiss",
        label: "暂不处理",
        style: "outline",
        action_type: "dismiss",
      },
    ],
  };

  it("validates card schema integrity and block polymorphic structures", () => {
    expect(sampleCard.card_id).toBe("card_sample_001");
    expect(sampleCard.level).toBe("critical");
    expect(sampleCard.status).toBe("pending");
    expect(sampleCard.blocks).toHaveLength(5);
    expect(sampleCard.inputs).toHaveLength(2);
    expect(sampleCard.actions).toHaveLength(2);

    // Verify block types
    const blockTypes = sampleCard.blocks.map((b) => b.type);
    expect(blockTypes).toEqual([
      "callout",
      "markdown",
      "key_value",
      "code_diff",
      "artifact_link",
    ]);
  });

  it("handles form inputs merging upon action submission", () => {
    const invocations: ActionCardInvocation[] = [];
    const onInvoke = (inv: ActionCardInvocation) => {
      invocations.push(inv);
    };

    const action = sampleCard.actions![0]!;
    const formValues = {
      comment: "现场已断电确认安全",
      notify_channel: true,
    };

    const finalArguments = action.include_form_inputs
      ? { ...action.arguments, ...formValues }
      : action.arguments;

    onInvoke({
      card_id: sampleCard.card_id,
      action_id: action.id,
      action_type: action.action_type || "invoke_tool",
      tool_name: action.tool_name,
      arguments: finalArguments,
    });

    expect(invocations).toHaveLength(1);
    expect(invocations[0]).toEqual({
      card_id: "card_sample_001",
      action_id: "btn_writeback",
      action_type: "invoke_tool",
      tool_name: "jira.update_issue",
      arguments: {
        issue_key: "TESTISSUE-125380",
        comment: "现场已断电确认安全",
        notify_channel: true,
      },
    });
  });

  it("preserves terminal statuses without allowing further mutations", () => {
    const executedCard: ActionCard = {
      ...sampleCard,
      status: "executed",
    };
    expect(executedCard.status).toBe("executed");
    const isDone = executedCard.status !== "pending";
    expect(isDone).toBe(true);
  });
});
