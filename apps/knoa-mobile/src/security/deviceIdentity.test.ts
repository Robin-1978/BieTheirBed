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
  storeCoreSession,
  storeEventCursor,
  storeSession,
  updateNodeDirectGatewayUrl,
} from "./deviceIdentity";

beforeEach(() => native.cache.clear());

describe("Node binding selection", () => {
  it("does not carry session, event cursor, or direct URL across re-pairing", async () => {
    await replaceConnectionIdentity({
      nodeId: "node_1",
      displayName: "Old Desktop",
      deviceId: "device_old",
      gatewayUrl: "https://old.node.example",
      directGatewayUrl: "http://192.168.1.10:9531",
      nodeSigningPublicKey: "s".repeat(40),
      nodeConfigurationPublicKey: "c".repeat(40),
    });
    await storeSession("old-session", Date.now() / 1000 + 3600);
    await storeCoreSession("old-core-session");
    await storeEventCursor(42);

    await replaceConnectionIdentity({
      nodeId: "node_1",
      displayName: "New Desktop",
      deviceId: "device_new",
      gatewayUrl: "https://new.node.example",
      nodeSigningPublicKey: "n".repeat(40),
      nodeConfigurationPublicKey: "d".repeat(40),
    });

    await expect(loadConnectionIdentity()).resolves.toMatchObject({
      nodeId: "node_1",
      displayName: "New Desktop",
      deviceId: "device_new",
      gatewayUrl: "https://new.node.example",
      directGatewayUrl: "",
      sessionToken: "",
      sessionExpiresAt: 0,
      coreSessionHandle: "",
      eventCursor: 0,
      lastConnectedAt: 0,
    });
  });

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
    await updateNodeDirectGatewayUrl("node_1", "https://direct.node.example/");

    await deselectNode();

    await expect(loadConnectionIdentity()).resolves.toBeNull();
    await expect(listNodeBindings()).resolves.toEqual([
      expect.objectContaining({
        nodeId: "node_1",
        displayName: "Robin Desktop",
        directGatewayUrl: "https://direct.node.example",
        sessionToken: "",
      }),
    ]);
  });
});
