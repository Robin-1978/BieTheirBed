import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSerialPoller } from "./serialPoller";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("createSerialPoller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("runs at once and then waits the interval after every completed run", async () => {
    const attempts: number[] = [];
    const poller = createSerialPoller({
      intervalMs: 5000,
      run: async (attempt) => {
        attempts.push(attempt);
      },
    });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(attempts).toEqual([0]);

    await vi.advanceTimersByTimeAsync(4999);
    expect(attempts).toEqual([0]);
    await vi.advanceTimersByTimeAsync(1);
    expect(attempts).toEqual([0, 1]);

    await vi.advanceTimersByTimeAsync(5000);
    expect(attempts).toEqual([0, 1, 2]);
    poller.stop();
  });

  it("never overlaps runs when the server is slower than the interval", async () => {
    const gates: Array<ReturnType<typeof deferred>> = [];
    let inFlight = 0;
    let maxInFlight = 0;
    const poller = createSerialPoller({
      intervalMs: 5000,
      run: async () => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        const gate = deferred();
        gates.push(gate);
        await gate.promise;
        inFlight -= 1;
      },
    });

    poller.start();
    await vi.advanceTimersByTimeAsync(12000);
    expect(gates).toHaveLength(1);

    gates[0]?.resolve();
    await vi.advanceTimersByTimeAsync(4999);
    expect(gates).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(gates).toHaveLength(2);
    expect(maxInFlight).toBe(1);
    poller.stop();
  });

  it("keeps polling after a run fails", async () => {
    const run = vi
      .fn<(attempt: number) => Promise<void>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(undefined);
    const poller = createSerialPoller({ intervalMs: 1000, run });

    poller.start();
    await vi.advanceTimersByTimeAsync(1000);

    expect(run.mock.calls.map(([attempt]) => attempt)).toEqual([0, 1]);
    poller.stop();
  });

  it("stop cancels the pending timer and ignores a run that finishes afterwards", async () => {
    const gate = deferred();
    const run = vi.fn(async () => gate.promise);
    const poller = createSerialPoller({ intervalMs: 1000, run });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    gate.resolve();
    await vi.advanceTimersByTimeAsync(10_000);

    expect(run).toHaveBeenCalledTimes(1);
  });

  it("does not start a second schedule when start is called while running", async () => {
    const run = vi.fn(async () => undefined);
    const poller = createSerialPoller({ intervalMs: 1000, run });

    poller.start();
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(run).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("restarts cleanly and ignores the stale run of the previous start", async () => {
    const gates: Array<ReturnType<typeof deferred>> = [];
    const run = vi.fn(async () => {
      const gate = deferred();
      gates.push(gate);
      await gate.promise;
    });
    const poller = createSerialPoller({ intervalMs: 1000, run });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(run).toHaveBeenCalledTimes(2);

    gates[0]?.resolve();
    gates[1]?.resolve();
    await vi.advanceTimersByTimeAsync(1000);

    expect(run).toHaveBeenCalledTimes(3);
    poller.stop();
  });
});
