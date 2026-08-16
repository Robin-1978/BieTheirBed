import * as SecureStore from "expo-secure-store";

import { loadOrCreateInstallationId, loadOrCreatePrivateKey, publicKey } from "@/security/deviceIdentity";

const HUB_CONNECTION = "knoa.hub.connection.v1";

export type HubNode = {
  node_id: string;
  display_name: string;
  signing_public_key: string;
  configuration_public_key: string;
  platform: string;
  version: string;
  online: boolean;
  last_seen: number | null;
};

export type HubConnection = {
  url: string;
  token: string;
  hubId: string;
  signingPublicKey: string;
};

export type NodeEnrollmentGrant = {
  grant_id: string;
  secret: string;
  challenge: string;
  expires_at: number;
};

export async function connectHub(url: string, token: string, displayName: string): Promise<HubConnection> {
  const normalized = url.trim().replace(/\/$/, "");
  if (!/^https?:\/\//.test(normalized) || token.length < 32) throw new Error("Hub 地址或帐号令牌无效");
  const hub = await request<{ hub_id: string; signing_public_key: string }>(normalized, token, "/v1/hub");
  const privateKey = await loadOrCreatePrivateKey();
  await request(normalized, token, "/v1/installations", {
    method: "POST",
    body: {
      installation_id: await loadOrCreateInstallationId(),
      public_key: publicKey(privateKey),
      display_name: displayName.trim() || "Knoa App",
    },
  });
  const connection = {
    url: normalized,
    token,
    hubId: hub.hub_id,
    signingPublicKey: hub.signing_public_key,
  };
  await SecureStore.setItemAsync(HUB_CONNECTION, JSON.stringify(connection));
  return connection;
}

export async function loadHubConnection(): Promise<HubConnection | null> {
  const raw = await SecureStore.getItemAsync(HUB_CONNECTION);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as HubConnection;
    return parsed.url && parsed.token && parsed.hubId && parsed.signingPublicKey ? parsed : null;
  } catch {
    return null;
  }
}

export async function listHubNodes(): Promise<HubNode[]> {
  const connection = await loadHubConnection();
  if (!connection) return [];
  const result = await request<{ nodes: HubNode[] }>(connection.url, connection.token, "/v1/nodes");
  return result.nodes;
}

export async function createNodeEnrollmentGrant(): Promise<NodeEnrollmentGrant> {
  const connection = await requiredHubConnection();
  return request<NodeEnrollmentGrant>(connection.url, connection.token, "/v1/node-enrollment-grants", {
    method: "POST",
    body: { ttl_seconds: 600 },
  });
}

export async function issueConnectionTicket(
  nodeId: string,
  transport: "direct" | "relay",
): Promise<string> {
  const connection = await requiredHubConnection();
  const installationId = await loadOrCreateInstallationId();
  const response = await request<{ ticket: string }>(connection.url, connection.token, "/v1/connection-tickets", {
    method: "POST",
    body: { installation_id: installationId, node_id: nodeId, transport },
  });
  return response.ticket;
}

async function requiredHubConnection(): Promise<HubConnection> {
  const connection = await loadHubConnection();
  if (!connection) throw new Error("尚未连接 Personal Hub");
  return connection;
}

async function request<T>(
  url: string,
  token: string,
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const response = await fetch(`${url}${path}`, {
    method: options.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) throw new Error(response.status === 401 ? "Hub 帐号认证失败" : "Hub 请求失败");
  return response.json() as Promise<T>;
}
