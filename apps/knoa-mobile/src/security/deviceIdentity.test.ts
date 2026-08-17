import { beforeEach, describe, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({ cache: new Map<string, string>() }));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => native.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => { native.cache.set(key, value); }),
}));

vi.mock("expo-crypto", () => ({ getRandomBytesAsync: vi.fn() }));
vi.mock("@/api/base64", () => ({
  fromBase64Url: vi.fn(),
  toBase64Url: vi.fn(),
}));

import {
  deselectNode,
  listNodeBindings,
  loadConnectionIdentity,
  replaceConnectionIdentity,
  storeSession,
} from "./deviceIdentity";

beforeEach(() => native.cache.clear());

describe("Node binding selection", () => {
  it("disconnects the active Node without deleting its trust binding", async () => {
    await replaceConnectionIdentity({
      nodeId: "node_1",
      displayName: "Robin Desktop",
      deviceId: "device_1",
      gatewayUrl: "https://node.example",
      nodeSigningPublicKey: "s".repeat(40),
      nodeConfigurationPublicKey: "c".repeat(40),
    });
    await storeSession("session", Date.now() / 1000 + 3600);

    await deselectNode();

    await expect(loadConnectionIdentity()).resolves.toBeNull();
    await expect(listNodeBindings()).resolves.toEqual([
      expect.objectContaining({ nodeId: "node_1", displayName: "Robin Desktop", sessionToken: "" }),
    ]);
  });
});
