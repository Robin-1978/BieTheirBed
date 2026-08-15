import { describe, expect, it } from "vitest";

import { isPresentationTaskEvent, shouldRefreshExecution } from "./taskEventPolicy";

describe("task event presentation policy", () => {
  it("keeps high-frequency deltas out of shared React state", () => {
    expect(isPresentationTaskEvent("reasoning_delta")).toBe(false);
    expect(isPresentationTaskEvent("content_delta")).toBe(false);
    expect(isPresentationTaskEvent("approval_requested")).toBe(true);
    expect(isPresentationTaskEvent("completed")).toBe(true);
  });

  it("refreshes execution snapshots for meaningful step changes only", () => {
    expect(shouldRefreshExecution("tool_call")).toBe(true);
    expect(shouldRefreshExecution("tool_result")).toBe(true);
    expect(shouldRefreshExecution("reasoning_delta")).toBe(false);
  });
});
