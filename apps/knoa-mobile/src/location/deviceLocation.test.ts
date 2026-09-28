import { beforeEach, describe, expect, it, vi } from "vitest";

const secure = vi.hoisted(() => ({ cache: new Map<string, string>() }));
const expoLocation = vi.hoisted(() => ({
  permission: "granted" as string,
  position: { coords: { latitude: 39.9042, longitude: 116.4074, accuracy: 25 } },
  places: [{ city: "北京市", district: "朝阳区", street: "建国路", name: "88号" }] as Array<Record<string, string>>,
}));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => secure.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => { secure.cache.set(key, value); }),
  deleteItemAsync: vi.fn(async (key: string) => { secure.cache.delete(key); }),
}));

vi.mock("expo-location", () => ({
  LocationAccuracy: { Low: 1, Balanced: 3, High: 6 },
  getForegroundPermissionsAsync: vi.fn(async () => ({ status: expoLocation.permission, canAskAgain: true })),
  requestForegroundPermissionsAsync: vi.fn(async () => ({ status: expoLocation.permission })),
  getLastKnownPositionAsync: vi.fn(async () => null),
  getCurrentPositionAsync: vi.fn(async () => expoLocation.position),
  reverseGeocodeAsync: vi.fn(async () => expoLocation.places),
}));

const rn = vi.hoisted(() => ({ os: "ios", modules: {} as Record<string, unknown> }));

vi.mock("react-native", () => ({
  NativeModules: rn.modules,
  Platform: { get OS() { return rn.os; } },
}));

import {
  loadLocationPreference,
  resolveDeviceLocation,
  saveLocationPreference,
} from "./deviceLocation";

beforeEach(() => {
  secure.cache.clear();
  expoLocation.permission = "granted";
  expoLocation.position = { coords: { latitude: 39.9042, longitude: 116.4074, accuracy: 25 } };
  expoLocation.places = [{ city: "北京市", district: "朝阳区", street: "建国路", name: "88号" }];
  rn.os = "ios";
  for (const key of Object.keys(rn.modules)) delete rn.modules[key];
});

describe("device location", () => {
  it("stays off by default and resolves to empty", async () => {
    await expect(loadLocationPreference()).resolves.toEqual({ enabled: false, precision: "block" });
    await expect(resolveDeviceLocation()).resolves.toBe("");
  });

  it("formats a block-level address without coordinates", async () => {
    await saveLocationPreference({ enabled: true, precision: "block" });
    await expect(resolveDeviceLocation()).resolves.toBe("北京市朝阳区建国路88号");
  });

  it("falls back to city level when block parts are missing", async () => {
    await saveLocationPreference({ enabled: true, precision: "city" });
    expoLocation.places = [{ city: "北京市", district: "", street: "", name: "" }];
    await expect(resolveDeviceLocation()).resolves.toBe("北京市");
  });

  it("appends coordinates for precise mode", async () => {
    await saveLocationPreference({ enabled: true, precision: "precise" });
    await expect(resolveDeviceLocation()).resolves.toBe(
      "北京市朝阳区建国路88号 (39.9042,116.4074 ±25m)",
    );
  });

  it("resolves to empty when permission is denied", async () => {
    await saveLocationPreference({ enabled: true, precision: "block" });
    expoLocation.permission = "denied";
    await expect(resolveDeviceLocation()).resolves.toBe("");
  });

  it("falls back to the GMS-independent native fix on android", async () => {
    const Location = await import("expo-location");
    vi.mocked(Location.getCurrentPositionAsync).mockRejectedValueOnce(new Error("fused unavailable"));
    vi.mocked(Location.reverseGeocodeAsync).mockResolvedValueOnce([]);
    rn.os = "android";
    rn.modules.KnoaLocation = {
      getFix: async () => ({
        latitude: 39.9042,
        longitude: 116.4074,
        accuracy: 150,
        provider: "network",
        timestamp: Date.now(),
      }),
    };
    await saveLocationPreference({ enabled: true, precision: "precise" });
    await expect(resolveDeviceLocation()).resolves.toBe(
      "未知位置 (39.9042,116.4074 ±150m)",
    );
  });
});
