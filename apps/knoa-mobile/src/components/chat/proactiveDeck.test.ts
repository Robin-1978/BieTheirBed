import { describe, expect, it } from "vitest";

import { DECK_ACTIONS } from "./proactiveDeckModel";

describe("proactiveDeckModel and coworker core actions", () => {
  it("defines standard pro-active action items for zero cold-start", () => {
    expect(DECK_ACTIONS.length).toBeGreaterThanOrEqual(4);

    const keys = DECK_ACTIONS.map((action) => action.key);
    expect(keys).toContain("clean");
    expect(keys).toContain("health");
    expect(keys).toContain("git");
    expect(keys).toContain("briefing");
  });

  it("ensures each action has non-empty prompts, task titles and descriptive instructions", () => {
    for (const action of DECK_ACTIONS) {
      expect(action.key.trim().length).toBeGreaterThan(0);
      expect(action.prompt.trim().length).toBeGreaterThan(15);
      expect(action.taskTitle.trim().length).toBeGreaterThan(4);
      expect(["folder", "pulse", "code", "globe"]).toContain(action.icon);
    }
  });

  it("ensures all title and desc translation keys follow the chat.deck prefix pattern", () => {
    for (const action of DECK_ACTIONS) {
      expect(action.titleKey).toMatch(/^chat\.deckAction[A-Za-z]+$/);
      expect(action.descKey).toMatch(/^chat\.deckAction[A-Za-z]+Desc$/);
    }
  });
});
