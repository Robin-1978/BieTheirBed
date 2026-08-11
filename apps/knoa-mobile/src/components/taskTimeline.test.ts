import { describe, expect, it } from "vitest";

import type { TaskTraceEntry } from "@/api/models";
import { mergeTaskTimeline } from "./taskTimeline";

function entry(overrides: Partial<TaskTraceEntry>): TaskTraceEntry {
  return {
    entry_type: "reasoning",
    iteration: 1,
    content: "",
    tool_call_id: "",
    tool_name: "",
    tool_args: {},
    tool_result: null,
    artifact: null,
    occurred_at: 1,
    ...overrides,
  };
}

describe("mergeTaskTimeline", () => {
  it("updates a tool call in place when its result arrives", () => {
    expect(mergeTaskTimeline([
      entry({ entry_type: "tool_call", tool_call_id: "call-1", tool_name: "schedule_task" }),
      entry({ entry_type: "reasoning", content: "继续处理", occurred_at: 2 }),
      entry({ entry_type: "tool_result", tool_call_id: "call-1", tool_name: "schedule_task", occurred_at: 3 }),
    ])).toEqual([
      { kind: "tool", key: "tool:call-1", toolName: "schedule_task", state: "completed" },
      { kind: "entry", key: "reasoning:2:1", entry: entry({ entry_type: "reasoning", content: "继续处理", occurred_at: 2 }) },
    ]);
  });
});
