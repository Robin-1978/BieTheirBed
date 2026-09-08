import { describe, expect, it } from "vitest";

import type { ChatTurnSnapshot } from "@/api/models";
import {
  extractActionCards,
  isActionCardLike,
  stripActionCardMarkdownBlocks,
} from "./actionCardExtractor";

function makeTurn(overrides: Partial<ChatTurnSnapshot> = {}): ChatTurnSnapshot {
  return {
    turn_id: "turn_001",
    session_handle: "session_001",
    client_request_id: "req_001",
    user_input: "排查现场机器人故障",
    attachments: [],
    tools_enabled: true,
    state: "completed",
    reasoning: "",
    content: "",
    final_output: "",
    artifacts: [],
    failure_code: "",
    cancel_requested: false,
    tool_steps: [],
    approvals: [],
    timeline: [],
    created_at: 1000,
    updated_at: 1000,
    finished_at: 1005,
    revision: 1,
    ...overrides,
  };
}

describe("actionCardExtractor", () => {
  it("validates valid and invalid ActionCard structures", () => {
    expect(
      isActionCardLike({
        card_id: "card_1",
        title: "Test Card",
        level: "info",
        blocks: [],
      })
    ).toBe(true);

    expect(isActionCardLike(null)).toBe(false);
    expect(isActionCardLike("string")).toBe(false);
    expect(isActionCardLike({ card_id: "" })).toBe(false);
    expect(isActionCardLike({ card_id: "c1", title: "Missing blocks" })).toBe(false);
  });

  it("extracts ActionCard from tool_steps results", () => {
    const cardData = {
      schema_version: "1.0",
      card_id: "card_tool_step_01",
      title: "诊断报告卡片",
      level: "critical",
      status: "pending",
      source: { plugin_name: "jira", created_at: 1000 },
      blocks: [
        { type: "callout", level: "error", text: "段错误崩溃" },
      ],
    };

    const turn = makeTurn({
      tool_steps: [
        {
          tool_name: "jira.investigate_defect",
          tool_result: {
            success: true,
            action_card: cardData,
          },
        },
      ],
    });

    const cards = extractActionCards(turn);
    expect(cards).toHaveLength(1);
    expect(cards[0]?.card_id).toBe("card_tool_step_01");
    expect(cards[0]?.title).toBe("诊断报告卡片");
  });

  it("extracts ActionCard from embedded markdown action_card code fences", () => {
    const markdown = [
      "现场日志分析完毕，请核对并流转工单：",
      "```action_card",
      JSON.stringify({
        schema_version: "1.0",
        card_id: "card_md_fence_02",
        title: "回写建议案卷",
        level: "warning",
        status: "pending",
        source: { plugin_name: "jira", created_at: 1000 },
        blocks: [{ type: "markdown", content: "修复空指针引用" }],
      }, null, 2),
      "```",
      "核对无误后请点击卡片中的按钮执行。",
    ].join("\n");

    const turn = makeTurn({
      final_output: markdown,
    });

    const cards = extractActionCards(turn);
    expect(cards).toHaveLength(1);
    expect(cards[0]?.card_id).toBe("card_md_fence_02");
    expect(cards[0]?.level).toBe("warning");

    const stripped = stripActionCardMarkdownBlocks(markdown);
    expect(stripped).not.toContain("```action_card");
    expect(stripped).toContain("现场日志分析完毕，请核对并流转工单：");
    expect(stripped).toContain("核对无误后请点击卡片中的按钮执行。");
  });

  it("deduplicates action cards sharing the same card_id", () => {
    const cardData = {
      card_id: "card_duplicate_01",
      title: "重复卡片测试",
      level: "info",
      status: "pending",
      blocks: [],
    };

    const turn = makeTurn({
      action_cards: [cardData],
      tool_steps: [
        { tool_result: { action_card: cardData } },
      ],
    } as any);

    const cards = extractActionCards(turn);
    expect(cards).toHaveLength(1);
  });
});
