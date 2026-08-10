import { ed25519 } from "@noble/curves/ed25519.js";
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { fromBase64Url, toBase64Url } from "@/api/base64";

const PRIVATE_KEY = "knoa.gateway.ed25519.private.v1";
const CONNECTION_IDENTITY = "knoa.connection-identity.v2";
const LEGACY_KEYS = [
  "knoa.gateway.device-id.v1",
  "knoa.gateway.url.v1",
  "knoa.gateway.session-token.v1",
  "knoa.gateway.session-expiry.v1",
  "knoa.core.session-handle.v1",
  "knoa.gateway.event-cursor.v1",
] as const;

export type ConnectionIdentity = {
  version: 2;
  deviceId: string;
  gatewayUrl: string;
  sessionToken: string;
  sessionExpiresAt: number;
  coreSessionHandle: string;
  eventCursor: number;
  lastConnectedAt: number;
};

export type StoredDevice = Pick<ConnectionIdentity, "deviceId" | "gatewayUrl">;

let identityMutation: Promise<void> = Promise.resolve();

export async function loadOrCreatePrivateKey(): Promise<Uint8Array> {
  const stored = await SecureStore.getItemAsync(PRIVATE_KEY);
  if (stored) return fromBase64Url(stored);
  const key = await Crypto.getRandomBytesAsync(32);
  await SecureStore.setItemAsync(PRIVATE_KEY, toBase64Url(key));
  return key;
}

export function publicKey(privateKey: Uint8Array): string {
  return toBase64Url(ed25519.getPublicKey(privateKey));
}

export function sign(privateKey: Uint8Array, payload: string): string {
  return toBase64Url(ed25519.sign(new TextEncoder().encode(payload), privateKey));
}

export async function replaceConnectionIdentity(device: StoredDevice): Promise<void> {
  const next: ConnectionIdentity = {
    version: 2,
    deviceId: device.deviceId,
    gatewayUrl: device.gatewayUrl.replace(/\/$/, ""),
    sessionToken: "",
    sessionExpiresAt: 0,
    coreSessionHandle: "",
    eventCursor: 0,
    lastConnectedAt: 0,
  };
  await queueIdentityMutation(async () => {
    await SecureStore.setItemAsync(CONNECTION_IDENTITY, JSON.stringify(next));
    await Promise.all(LEGACY_KEYS.map((key) => SecureStore.deleteItemAsync(key)));
  });
}

export async function loadConnectionIdentity(): Promise<ConnectionIdentity | null> {
  const raw = await SecureStore.getItemAsync(CONNECTION_IDENTITY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<ConnectionIdentity>;
    if (
      parsed.version !== 2
      || typeof parsed.deviceId !== "string"
      || !parsed.deviceId
      || typeof parsed.gatewayUrl !== "string"
      || !/^https?:\/\//.test(parsed.gatewayUrl)
    ) return null;
    return {
      version: 2,
      deviceId: parsed.deviceId,
      gatewayUrl: parsed.gatewayUrl,
      sessionToken: typeof parsed.sessionToken === "string" ? parsed.sessionToken : "",
      sessionExpiresAt: typeof parsed.sessionExpiresAt === "number" ? parsed.sessionExpiresAt : 0,
      coreSessionHandle: typeof parsed.coreSessionHandle === "string" ? parsed.coreSessionHandle : "",
      eventCursor: Number.isSafeInteger(parsed.eventCursor) && Number(parsed.eventCursor) >= 0 ? Number(parsed.eventCursor) : 0,
      lastConnectedAt: typeof parsed.lastConnectedAt === "number" ? parsed.lastConnectedAt : 0,
    };
  } catch {
    return null;
  }
}

export async function loadDevice(): Promise<StoredDevice | null> {
  const identity = await loadConnectionIdentity();
  return identity ? { deviceId: identity.deviceId, gatewayUrl: identity.gatewayUrl } : null;
}

export async function storeSession(token: string, expiresAt: number): Promise<void> {
  await updateIdentity((current) => ({
    ...current,
    sessionToken: token,
    sessionExpiresAt: expiresAt,
    lastConnectedAt: Date.now() / 1000,
  }));
}

export async function loadSessionToken(): Promise<string | null> {
  const identity = await loadConnectionIdentity();
  if (!identity?.sessionToken || identity.sessionExpiresAt <= Date.now() / 1000 + 30) return null;
  return identity.sessionToken;
}

export async function clearSession(): Promise<void> {
  await updateIdentity((current) => ({
    ...current,
    sessionToken: "",
    sessionExpiresAt: 0,
  }));
}

export async function loadCoreSession(): Promise<string | null> {
  return (await loadConnectionIdentity())?.coreSessionHandle || null;
}

export async function storeCoreSession(sessionHandle: string): Promise<void> {
  await updateIdentity((current) => ({ ...current, coreSessionHandle: sessionHandle }));
}

export async function loadEventCursor(): Promise<number> {
  return (await loadConnectionIdentity())?.eventCursor ?? 0;
}

export async function storeEventCursor(value: number): Promise<void> {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("事件游标无效");
  await updateIdentity((current) => ({
    ...current,
    eventCursor: Math.max(current.eventCursor, value),
  }));
}

export async function clearConnectionIdentity(): Promise<void> {
  await queueIdentityMutation(() => SecureStore.deleteItemAsync(CONNECTION_IDENTITY));
}

async function updateIdentity(
  transform: (current: ConnectionIdentity) => ConnectionIdentity,
): Promise<void> {
  await queueIdentityMutation(async () => {
    const current = await loadConnectionIdentity();
    if (!current) throw new Error("设备尚未配对");
    await SecureStore.setItemAsync(CONNECTION_IDENTITY, JSON.stringify(transform(current)));
  });
}

async function queueIdentityMutation(operation: () => Promise<void>): Promise<void> {
  const pending = identityMutation.then(operation, operation);
  identityMutation = pending.catch(() => undefined);
  await pending;
}
