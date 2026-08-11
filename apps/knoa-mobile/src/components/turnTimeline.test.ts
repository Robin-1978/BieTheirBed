import { describe, expect, it } from "vitest";

import type { ChatTimelineEntry } from "@/api/models";
import { timelineDisplayEntries } from "./turnTimeline";

function entry(updates: Partial<ChatTimelineEntry>): ChatTimelineEntry {
  return {
    kind: "reasoning",
    content: "",
    tool_call_id: "",
    tool_name: "",
    tool_args: {},
    tool_result: null,
    blocked: false,
    iteration: 1,
    ...updates,
  };
}

describe("timelineDisplayEntries", () => {
  it("updates a tool call row in place when its result arrives", () => {
    const rows = timelineDisplayEntries([
      entry({ kind: "reasoning", content: "先分析" }),
      entry({ kind: "tool_call", tool_call_id: "call-a", tool_name: "status" }),
      entry({ kind: "tool_result", tool_call_id: "call-a", tool_name: "status" }),
      entry({ kind: "reasoning", content: "继续判断", iteration: 2 }),
    ]);

    expect(rows).toEqual([
      { kind: "reasoning", key: "reasoning:1:0", content: "先分析" },
      { kind: "tool", key: "tool:call-a", toolName: "status", state: "completed" },
      { kind: "reasoning", key: "reasoning:2:3", content: "继续判断" },
    ]);
  });

  it("keeps running and failed tool states", () => {
    expect(timelineDisplayEntries([
      entry({ kind: "tool_call", tool_call_id: "call-a", tool_name: "read_file" }),
    ])[0]).toMatchObject({ state: "running" });
    expect(timelineDisplayEntries([
      entry({ kind: "tool_call", tool_call_id: "call-a", tool_name: "write_file" }),
      entry({ kind: "tool_result", tool_call_id: "call-a", tool_name: "write_file", blocked: true }),
    ])[0]).toMatchObject({ state: "failed" });
  });
});
