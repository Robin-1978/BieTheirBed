import { beforeEach, describe, expect, it, vi } from "vitest";

const secure = vi.hoisted(() => ({ cache: new Map<string, string>() }));
const resolver = vi.hoisted(() => ({ enabled: true, text: "" }));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => secure.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => { secure.cache.set(key, value); }),
  deleteItemAsync: vi.fn(async (key: string) => { secure.cache.delete(key); }),
}));

vi.mock("./deviceLocation", () => ({
  loadLocationPreference: vi.fn(async () => ({ enabled: resolver.enabled, precision: "block" })),
  resolveDeviceLocation: vi.fn(async () => resolver.text),
}));

beforeEach(async () => {
  vi.resetModules();
  secure.cache.clear();
  resolver.enabled = true;
  resolver.text = "";
  const mocked = await import("./deviceLocation");
  vi.mocked(mocked.resolveDeviceLocation).mockClear();
  vi.mocked(mocked.loadLocationPreference).mockClear();
});

async function cacheModule() {
  return import("./locationCache");
}

describe("location cache", () => {
  it("reads empty before the first refresh and never resolves live", async () => {
    const { getCachedLocationText } = await cacheModule();
    const { resolveDeviceLocation } = await import("./deviceLocation");
    await expect(getCachedLocationText()).resolves.toBe("");
    expect(vi.mocked(resolveDeviceLocation)).not.toHaveBeenCalled();
  });

  it("stores a good fix and serves it from memory afterwards", async () => {
    resolver.text = "北京市朝阳区建国路88号";
    const { getCachedLocationText, refreshLocationCache } = await cacheModule();
    const { resolveDeviceLocation } = await import("./deviceLocation");
    await refreshLocationCache();
    await expect(getCachedLocationText()).resolves.toBe("北京市朝阳区建国路88号");
    expect(vi.mocked(resolveDeviceLocation)).toHaveBeenCalledTimes(1);
  });

  it("never poisons a good fix with an empty refresh", async () => {
    resolver.text = "北京市朝阳区建国路88号";
    const { getCachedLocationText, refreshLocationCache } = await cacheModule();
    await refreshLocationCache();
    resolver.text = "";
    await refreshLocationCache();
    await expect(getCachedLocationText()).resolves.toBe("北京市朝阳区建国路88号");
  });

  it("skips refresh entirely when the switch is off", async () => {
    resolver.enabled = false;
    resolver.text = "北京市朝阳区建国路88号";
    const { getCachedLocationText, refreshLocationCache } = await cacheModule();
    const { resolveDeviceLocation } = await import("./deviceLocation");
    await refreshLocationCache();
    await expect(getCachedLocationText()).resolves.toBe("");
    expect(vi.mocked(resolveDeviceLocation)).not.toHaveBeenCalled();
  });
});
