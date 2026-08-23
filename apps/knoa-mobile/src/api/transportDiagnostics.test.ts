import { beforeEach, describe, expect, it } from "vitest";

import {
  clearTransportDiagnostics,
  recentTransportProbes,
  recordTransportProbe,
  summarizeTransportProbes,
} from "./transportDiagnostics";

beforeEach(() => {
  clearTransportDiagnostics();
});

describe("transport diagnostics", () => {
  it("keeps only the most recent probes", () => {
    for (let index = 0; index < 80; index += 1) {
      recordTransportProbe({ at: index, mode: "relay", path: "/v1/tasks", durationMs: 10, ok: true });
    }
    expect(recentTransportProbes()).toHaveLength(60);
    expect(recentTransportProbes()[0]?.at).toBe(20);
  });

  it("summarizes per-mode counts, failures, and average duration", () => {
    recordTransportProbe({ at: 1, mode: "direct", path: "/a", durationMs: 100, ok: true });
    recordTransportProbe({ at: 2, mode: "direct", path: "/a", durationMs: 200, ok: false });
    recordTransportProbe({ at: 3, mode: "relay", path: "/b", durationMs: 400, ok: true });
    const summary = summarizeTransportProbes();
    expect(summary.total).toBe(3);
    expect(summary.failed).toBe(1);
    expect(summary.averageMs).toBe(233);
    expect(summary.byMode.direct).toEqual({ count: 2, failed: 1 });
    expect(summary.byMode.relay).toEqual({ count: 1, failed: 0 });
    expect(summary.byMode.p2p).toEqual({ count: 0, failed: 0 });
  });

  it("reports an empty summary without dividing by zero", () => {
    const summary = summarizeTransportProbes();
    expect(summary.total).toBe(0);
    expect(summary.averageMs).toBe(0);
  });
});
