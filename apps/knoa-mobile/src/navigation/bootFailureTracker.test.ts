import { beforeEach, describe, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({ cache: new Map<string, string>() }));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => native.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => {
    native.cache.set(key, value);
  }),
  deleteItemAsync: vi.fn(async (key: string) => {
    native.cache.delete(key);
  }),
}));

import {
  recordBootFailure,
  recordBootSuccess,
  shouldEnterSafeMode,
} from "./bootFailureTracker";

describe("bootFailureTracker", () => {
  beforeEach(() => {
    native.cache.clear();
  });

  it("stays out of safe mode below the threshold", async () => {
    expect(await shouldEnterSafeMode()).toBe(false);
    await recordBootFailure();
    await recordBootFailure();
    expect(await shouldEnterSafeMode()).toBe(false);
  });

  it("enters safe mode after three consecutive failures", async () => {
    await recordBootFailure();
    await recordBootFailure();
    await recordBootFailure();
    expect(await shouldEnterSafeMode()).toBe(true);
  });

  it("resets the counter on success", async () => {
    await recordBootFailure();
    await recordBootFailure();
    await recordBootSuccess();
    expect(await shouldEnterSafeMode()).toBe(false);
    await recordBootFailure();
    await recordBootFailure();
    expect(await shouldEnterSafeMode()).toBe(false);
    await recordBootFailure();
    expect(await shouldEnterSafeMode()).toBe(true);
  });
});
