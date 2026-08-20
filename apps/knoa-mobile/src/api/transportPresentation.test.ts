import { describe, expect, it } from "vitest";

import { transportCompactLabel, transportDetail, transportLabel } from "./transportPresentation";

describe("transport presentation", () => {
  it.each([
    ["direct", "Direct 直连", "Direct"],
    ["p2p", "WebRTC P2P", "P2P"],
    ["relay", "Hub Relay", "Relay"],
  ] as const)("presents %s without confusing route intent with active transport", (mode, label, compact) => {
    expect(transportLabel(mode)).toBe(label);
    expect(transportCompactLabel(mode)).toBe(compact);
    expect(transportDetail(mode).length).toBeGreaterThan(10);
  });
});
