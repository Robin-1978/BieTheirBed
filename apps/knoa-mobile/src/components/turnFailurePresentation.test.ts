import { describe, expect, it } from "vitest";

import type { ChatTurnSnapshot } from "@/api/models";
import { turnFailureMessage } from "./turnFailurePresentation";

const t = (key: string, values?: Record<string, string | number>) => values?.code ? `${key}:${values.code}` : key;

function turn(failureCode: string, mediaType = "image/jpeg"): ChatTurnSnapshot {
  return {
    turn_id: "turn",
    session_handle: "session",
    client_request_id: "request",
    user_input: "input",
    attachments: mediaType ? [{ artifact_id: "artifact", caption: "photo.jpg" }] : [],
    tools_enabled: true,
    state: "failed",
    reasoning: "",
    content: "",
    final_output: "",
    artifacts: [],
    failure_code: failureCode,
    cancel_requested: false,
    tool_steps: [],
    approvals: [],
    timeline: [],
    created_at: 1,
    updated_at: 1,
    finished_at: 1,
    revision: 1,
  };
}

describe("turnFailureMessage", () => {
  it("explains image capability failures instead of showing only retry", () => {
    expect(turnFailureMessage(turn("unsupported_input"), t)).toBe("turn.failure.imageUnsupported");
  });

  it("keeps unknown durable failure codes visible", () => {
    expect(turnFailureMessage(turn("custom_failure", ""), t)).toBe("turn.failure.other:custom_failure");
  });

  it("explains a missing dedicated vision model", () => {
    expect(turnFailureMessage(turn("vision_unavailable"), t)).toBe("turn.failure.visionUnavailable");
  });
});
