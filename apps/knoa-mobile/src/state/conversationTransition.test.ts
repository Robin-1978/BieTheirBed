import { describe, expect, it } from "vitest";

import { shouldResetConversation } from "./conversationTransition";

describe("shouldResetConversation", () => {
  it("keeps the optimistic first turn when a provisional conversation is committed", () => {
    expect(shouldResetConversation("", "session-new")).toBe(false);
  });

  it("resets visible turns when switching between persisted conversations", () => {
    expect(shouldResetConversation("session-a", "session-b")).toBe(true);
  });

  it("resets the old conversation when starting a new topic", () => {
    expect(shouldResetConversation("session-a", "")).toBe(true);
  });
});
