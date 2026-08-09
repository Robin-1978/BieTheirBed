import { describe, expect, it } from "vitest";

import { EventCursor } from "./eventCursor";

describe("EventCursor", () => {
  it("persists only strictly newer feed event IDs", async () => {
    let stored = 4;
    const cursor = new EventCursor({
      load: async () => stored,
      save: async (value) => { stored = value; },
    });
    expect(await cursor.initialize()).toBe(4);
    expect(await cursor.accept(4)).toBe(false);
    expect(await cursor.accept(5)).toBe(true);
    expect(stored).toBe(5);
  });
});
