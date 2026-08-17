import { ed25519 } from "@noble/curves/ed25519.js";
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { fromBase64Url, toBase64Url } from "@/api/base64";

const PRIVATE_KEY = "knoa.app-installation.ed25519.private.v1";
const INSTALLATION_ID = "knoa.app-installation.id.v1";
const CONNECTION_VAULT = "knoa.node-bindings.v3";

export type NodeDeviceBinding = {
  nodeId: string;
  displayName: string;
  deviceId: string;
  gatewayUrl: string;
  nodeSigningPublicKey: string;
  nodeConfigurationPublicKey: string;
  sessionToken: string;
  sessionExpiresAt: number;
  coreSessionHandle: string;
  eventCursor: number;
  lastConnectedAt: number;
};

type ConnectionVault = {
  version: 3;
  activeNodeId: string;
  nodes: Record<string, NodeDeviceBinding>;
};

export type ConnectionIdentity = NodeDeviceBinding & { version: 3 };
export type StoredDevice = Pick<NodeDeviceBinding, "nodeId" | "deviceId" | "gatewayUrl">;

let identityMutation: Promise<void> = Promise.resolve();

export async function loadOrCreatePrivateKey(): Promise<Uint8Array> {
  const stored = await SecureStore.getItemAsync(PRIVATE_KEY);
  if (stored) return fromBase64Url(stored);
  const key = await Crypto.getRandomBytesAsync(32);
  await SecureStore.setItemAsync(PRIVATE_KEY, toBase64Url(key));
  return key;
}

export async function loadOrCreateInstallationId(): Promise<string> {
  const stored = await SecureStore.getItemAsync(INSTALLATION_ID);
  if (stored) return stored;
  const value = `app_${toBase64Url(await Crypto.getRandomBytesAsync(18))}`;
  await SecureStore.setItemAsync(INSTALLATION_ID, value);
  return value;
}

export function publicKey(privateKey: Uint8Array): string {
  return toBase64Url(ed25519.getPublicKey(privateKey));
}

export function sign(privateKey: Uint8Array, payload: string): string {
  return toBase64Url(ed25519.sign(new TextEncoder().encode(payload), privateKey));
}

export async function replaceConnectionIdentity(input: {
  nodeId: string;
  displayName?: string;
  deviceId: string;
  gatewayUrl: string;
  nodeSigningPublicKey: string;
  nodeConfigurationPublicKey: string;
}): Promise<void> {
  await queueIdentityMutation(async () => {
    const vault = await loadVault();
    const current = vault.nodes[input.nodeId];
    vault.nodes[input.nodeId] = {
      nodeId: input.nodeId,
      displayName: input.displayName?.trim() || current?.displayName || input.nodeId,
      deviceId: input.deviceId,
      gatewayUrl: input.gatewayUrl.replace(/\/$/, ""),
      nodeSigningPublicKey: input.nodeSigningPublicKey,
      nodeConfigurationPublicKey: input.nodeConfigurationPublicKey,
      sessionToken: "",
      sessionExpiresAt: 0,
      coreSessionHandle: current?.coreSessionHandle ?? "",
      eventCursor: current?.eventCursor ?? 0,
      lastConnectedAt: current?.lastConnectedAt ?? 0,
    };
    vault.activeNodeId = input.nodeId;
    await saveVault(vault);
  });
}

export async function loadConnectionIdentity(): Promise<ConnectionIdentity | null> {
  const vault = await loadVault();
  const binding = vault.nodes[vault.activeNodeId];
  return binding ? { version: 3, ...binding } : null;
}

export async function listNodeBindings(): Promise<NodeDeviceBinding[]> {
  const vault = await loadVault();
  return Object.values(vault.nodes).sort((left, right) =>
    right.lastConnectedAt - left.lastConnectedAt || left.displayName.localeCompare(right.displayName),
  );
}

export async function selectNode(nodeId: string): Promise<void> {
  await queueIdentityMutation(async () => {
    const vault = await loadVault();
    if (!vault.nodes[nodeId]) throw new Error("节点尚未配对");
    vault.activeNodeId = nodeId;
    await saveVault(vault);
  });
}

export async function deselectNode(): Promise<void> {
  await queueIdentityMutation(async () => {
    const vault = await loadVault();
    const current = vault.nodes[vault.activeNodeId];
    if (current) {
      vault.nodes[vault.activeNodeId] = {
        ...current,
        sessionToken: "",
        sessionExpiresAt: 0,
      };
    }
    vault.activeNodeId = "";
    await saveVault(vault);
  });
}

export async function loadDevice(): Promise<StoredDevice | null> {
  const identity = await loadConnectionIdentity();
  return identity
    ? { nodeId: identity.nodeId, deviceId: identity.deviceId, gatewayUrl: identity.gatewayUrl }
    : null;
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
  await updateIdentity((current) => ({ ...current, sessionToken: "", sessionExpiresAt: 0 }));
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
  await updateIdentity((current) => ({ ...current, eventCursor: Math.max(current.eventCursor, value) }));
}

export async function clearConnectionIdentity(): Promise<void> {
  await queueIdentityMutation(async () => {
    const vault = await loadVault();
    delete vault.nodes[vault.activeNodeId];
    vault.activeNodeId = "";
    await saveVault(vault);
  });
}

async function updateIdentity(
  transform: (current: NodeDeviceBinding) => NodeDeviceBinding,
): Promise<void> {
  await queueIdentityMutation(async () => {
    const vault = await loadVault();
    const current = vault.nodes[vault.activeNodeId];
    if (!current) throw new Error("设备尚未配对");
    vault.nodes[vault.activeNodeId] = transform(current);
    await saveVault(vault);
  });
}

async function loadVault(): Promise<ConnectionVault> {
  const raw = await SecureStore.getItemAsync(CONNECTION_VAULT);
  if (!raw) return { version: 3, activeNodeId: "", nodes: {} };
  try {
    const parsed = JSON.parse(raw) as Partial<ConnectionVault>;
    if (parsed.version !== 3 || typeof parsed.nodes !== "object" || !parsed.nodes) throw new Error();
    const nodes: Record<string, NodeDeviceBinding> = {};
    for (const [nodeId, candidate] of Object.entries(parsed.nodes)) {
      if (!validBinding(candidate, nodeId)) continue;
      nodes[nodeId] = candidate;
    }
    const activeNodeId = typeof parsed.activeNodeId === "string"
      && (parsed.activeNodeId === "" || Boolean(nodes[parsed.activeNodeId]))
      ? parsed.activeNodeId
      : "";
    return { version: 3, activeNodeId, nodes };
  } catch {
    return { version: 3, activeNodeId: "", nodes: {} };
  }
}

function validBinding(value: unknown, nodeId: string): value is NodeDeviceBinding {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<NodeDeviceBinding>;
  return item.nodeId === nodeId
    && typeof item.deviceId === "string" && Boolean(item.deviceId)
    && typeof item.gatewayUrl === "string" && /^https?:\/\//.test(item.gatewayUrl)
    && typeof item.nodeSigningPublicKey === "string" && item.nodeSigningPublicKey.length >= 40
    && typeof item.nodeConfigurationPublicKey === "string" && item.nodeConfigurationPublicKey.length >= 40;
}

async function saveVault(vault: ConnectionVault): Promise<void> {
  await SecureStore.setItemAsync(CONNECTION_VAULT, JSON.stringify(vault));
}

async function queueIdentityMutation(operation: () => Promise<void>): Promise<void> {
  const pending = identityMutation.then(operation, operation);
  identityMutation = pending.catch(() => undefined);
  await pending;
}
