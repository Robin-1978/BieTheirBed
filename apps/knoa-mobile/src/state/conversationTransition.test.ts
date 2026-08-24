import { describe, expect, it } from "vitest";

import {
  requireMatchingConversationAgent,
  resolveNewConversationAgent,
  shouldResetConversation,
} from "./conversationTransition";

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

describe("conversation agent selection", () => {
  it("never silently falls back when an explicit agent is unavailable", () => {
    expect(() => resolveNewConversationAgent("codex", ["knoa"], "knoa"))
      .toThrow(/codex.*不可用/);
  });

  it("accepts and verifies an explicit Codex session binding", () => {
    expect(resolveNewConversationAgent("codex", ["knoa", "codex"], "knoa"))
      .toBe("codex");
    expect(() => requireMatchingConversationAgent("codex", "knoa"))
      .toThrow(/绑定不一致/);
    expect(() => requireMatchingConversationAgent("codex", "codex"))
      .not.toThrow();
  });
});
