import { describe, expect, it } from "vitest";

import { recommendNodeId } from "./nodeRecommendation";

describe("recommendNodeId", () => {
  it("prefers the current node when bound", () => {
    expect(recommendNodeId(["a", "b"], ["b"], "a")).toBe("a");
  });

  it("falls back to an online bound node", () => {
    expect(recommendNodeId(["a", "b"], ["b"], "")).toBe("b");
  });

  it("falls back to first bound node when Hub state unknown", () => {
    expect(recommendNodeId(["a", "b"], null, "")).toBe("a");
  });

  it("falls back to first bound node when none online", () => {
    expect(recommendNodeId(["a", "b"], ["z"], "")).toBe("a");
  });

  it("returns null without bindings", () => {
    expect(recommendNodeId([], ["a"], "")).toBeNull();
  });
});
