import { describe, expect, it } from "vitest";

import {
  transportCompactLabelKey,
  transportDetailKey,
  transportLabelKey,
} from "./transportPresentation";

describe("transport presentation", () => {
  it.each([
    ["direct", "transport.direct", "transport.compact.direct", "transport.detail.direct"],
    ["p2p", "transport.p2p", "transport.compact.p2p", "transport.detail.p2p"],
    ["relay", "transport.relay", "transport.compact.relay", "transport.detail.relay"],
  ] as const)("maps %s to i18n keys without embedding locale-specific labels", (mode, label, compact, detail) => {
    expect(transportLabelKey(mode)).toBe(label);
    expect(transportCompactLabelKey(mode)).toBe(compact);
    expect(transportDetailKey(mode)).toBe(detail);
  });
});
