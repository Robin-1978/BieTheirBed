import { describe, expect, it } from "vitest";

import { createRequestSequence } from "./requestSequence";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("createRequestSequence", () => {
  it("treats only the most recently started request as the latest", () => {
    const sequence = createRequestSequence();

    const first = sequence.begin();
    expect(sequence.isLatest(first)).toBe(true);

    const second = sequence.begin();
    expect(sequence.isLatest(first)).toBe(false);
    expect(sequence.isLatest(second)).toBe(true);
  });

  it("cancel invalidates every request that is still in flight", () => {
    const sequence = createRequestSequence();
    const inFlight = sequence.begin();

    sequence.cancel();

    expect(sequence.isLatest(inFlight)).toBe(false);
  });

  it("lets a request started after a cancel be the latest again", () => {
    const sequence = createRequestSequence();
    sequence.cancel();

    expect(sequence.isLatest(sequence.begin())).toBe(true);
  });

  it("keeps independent sequences independent", () => {
    const opens = createRequestSequence();
    const others = createRequestSequence();
    const open = opens.begin();

    others.begin();
    others.cancel();

    expect(opens.isLatest(open)).toBe(true);
  });

  it("keeps the last tap when responses arrive out of order", async () => {
    const sequence = createRequestSequence();
    const shown: string[] = [];
    const open = async (response: Promise<string>) => {
      const ticket = sequence.begin();
      const detail = await response;
      if (sequence.isLatest(ticket)) shown.push(detail);
    };
    const slow = deferred<string>();
    const fast = deferred<string>();

    const tapA = open(slow.promise);
    const tapB = open(fast.promise);
    fast.resolve("detail B");
    await tapB;
    slow.resolve("detail A");
    await tapA;

    expect(shown).toEqual(["detail B"]);
  });
});
