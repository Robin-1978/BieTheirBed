import { describe, expect, it } from "vitest";

import type { ChatTurnSnapshot } from "@/api/models";
import { mergeConversationTurns } from "./conversationMerge";

function turn(id: string, revision: number, createdAt: number): ChatTurnSnapshot {
  return {
    turn_id: id,
    session_handle: "session-a",
    client_request_id: `request-${id}`,
    user_input: id,
    attachments: [],
    tools_enabled: true,
    state: revision > 1 ? "completed" : "running",
    reasoning: "",
    content: revision > 1 ? "done" : "",
    final_output: revision > 1 ? "done" : "",
    artifacts: [],
    failure_code: "",
    cancel_requested: false,
    tool_steps: [],
    approvals: [],
    timeline: [],
    created_at: createdAt,
    updated_at: createdAt,
    finished_at: null,
    revision,
  };
}

describe("mergeConversationTurns", () => {
  it("keeps cached history and prefers the newest server revision", () => {
    expect(mergeConversationTurns(
      [turn("older", 2, 1), turn("latest", 1, 2)],
      [turn("latest", 3, 2)],
    )).toEqual([turn("older", 2, 1), turn("latest", 3, 2)]);
  });
});
