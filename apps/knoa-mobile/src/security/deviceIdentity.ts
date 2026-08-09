import { ed25519 } from "@noble/curves/ed25519.js";
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { fromBase64Url, toBase64Url } from "@/api/base64";

const PRIVATE_KEY = "knoa.gateway.ed25519.private.v1";
const DEVICE_ID = "knoa.gateway.device-id.v1";
const GATEWAY_URL = "knoa.gateway.url.v1";
const SESSION_TOKEN = "knoa.gateway.session-token.v1";
const SESSION_EXPIRY = "knoa.gateway.session-expiry.v1";
const CORE_SESSION = "knoa.core.session-handle.v1";

export type StoredDevice = {
  deviceId: string;
  gatewayUrl: string;
};

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

export async function storeDevice(device: StoredDevice): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(DEVICE_ID, device.deviceId),
    SecureStore.setItemAsync(GATEWAY_URL, device.gatewayUrl),
  ]);
}

export async function loadDevice(): Promise<StoredDevice | null> {
  const [deviceId, gatewayUrl] = await Promise.all([
    SecureStore.getItemAsync(DEVICE_ID),
    SecureStore.getItemAsync(GATEWAY_URL),
  ]);
  return deviceId && gatewayUrl ? { deviceId, gatewayUrl } : null;
}

export async function storeSession(token: string, expiresAt: number): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(SESSION_TOKEN, token),
    SecureStore.setItemAsync(SESSION_EXPIRY, String(expiresAt)),
  ]);
}

export async function loadSessionToken(): Promise<string | null> {
  const [token, expiry] = await Promise.all([
    SecureStore.getItemAsync(SESSION_TOKEN),
    SecureStore.getItemAsync(SESSION_EXPIRY),
  ]);
  if (!token || !expiry || Number(expiry) <= Date.now() / 1000 + 30) return null;
  return token;
}

export async function clearSession(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(SESSION_TOKEN),
    SecureStore.deleteItemAsync(SESSION_EXPIRY),
  ]);
}

export async function loadCoreSession(): Promise<string | null> {
  return SecureStore.getItemAsync(CORE_SESSION);
}

export async function storeCoreSession(sessionHandle: string): Promise<void> {
  await SecureStore.setItemAsync(CORE_SESSION, sessionHandle);
}
