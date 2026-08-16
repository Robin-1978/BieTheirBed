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
  workspaceId: string;
  identityIssuerId: string;
  signingPublicKey: string;
};

export type WorkspaceModelResource = {
  resource_id: string;
  revision: number;
  canonical_digest: string;
  display_name: string;
  provider_protocol: "openai_compatible" | "anthropic";
  model_identity: string;
  declared_capabilities: Record<string, unknown>;
};

export type WorkspaceModelDeployment = {
  deployment_id: string;
  resource_id: string;
  resource_revision: number;
  target_node_id: string;
  desired_revision: number;
  enabled: boolean;
};

export type WorkspaceResourceGrant = {
  grant_id: string;
  caller_node_id: string;
  target_deployment_id: string;
  max_request_deadline: number;
  expires_at: number;
  revoked_at: number | null;
};

export type DeploymentObservation = {
  deployment_id: string;
  node_id: string;
  applied_digest: string;
  health: "healthy" | "degraded" | "unavailable";
  available_capacity: number;
  observed_at: number;
  expires_at: number;
};

export type WorkspaceResourceState = {
  resources: WorkspaceModelResource[];
  deployments: WorkspaceModelDeployment[];
  grants: WorkspaceResourceGrant[];
  observations: DeploymentObservation[];
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
  const hub = await request<{
    hub_id: string;
    workspace_id: string;
    identity_issuer_id: string;
    signing_public_key: string;
  }>(normalized, token, "/v1/hub");
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
    workspaceId: hub.workspace_id,
    identityIssuerId: hub.identity_issuer_id,
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
    if (!parsed.url || !parsed.token || !parsed.hubId || !parsed.signingPublicKey) return null;
    return {
      ...parsed,
      workspaceId: parsed.workspaceId || parsed.hubId,
      identityIssuerId: parsed.identityIssuerId || parsed.hubId,
    };
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

export async function loadWorkspaceResourceState(): Promise<WorkspaceResourceState> {
  const connection = await requiredHubConnection();
  const [resources, deployments, grants, observations] = await Promise.all([
    request<{ resources: WorkspaceModelResource[] }>(connection.url, connection.token, "/v1/model-resources"),
    request<{ deployments: WorkspaceModelDeployment[] }>(connection.url, connection.token, "/v1/model-deployments"),
    request<{ grants: WorkspaceResourceGrant[] }>(connection.url, connection.token, "/v1/resource-grants"),
    request<{ observations: DeploymentObservation[] }>(connection.url, connection.token, "/v1/deployment-observations"),
  ]);
  return {
    resources: resources.resources,
    deployments: deployments.deployments,
    grants: grants.grants,
    observations: observations.observations,
  };
}

export async function putWorkspaceModelResource(
  resource: WorkspaceModelResource,
): Promise<WorkspaceModelResource> {
  const connection = await requiredHubConnection();
  const result = await request<{ resource: WorkspaceModelResource }>(connection.url, connection.token, "/v1/model-resources", {
    method: "POST",
    body: resource,
  });
  return result.resource;
}

export async function putWorkspaceModelDeployment(
  deployment: WorkspaceModelDeployment,
): Promise<WorkspaceModelDeployment> {
  const connection = await requiredHubConnection();
  const result = await request<{ deployment: WorkspaceModelDeployment }>(connection.url, connection.token, "/v1/model-deployments", {
    method: "POST",
    body: deployment,
  });
  return result.deployment;
}

export async function putWorkspaceResourceGrant(
  grant: Omit<WorkspaceResourceGrant, "revoked_at">,
): Promise<WorkspaceResourceGrant> {
  const connection = await requiredHubConnection();
  const result = await request<{ grant: WorkspaceResourceGrant }>(connection.url, connection.token, "/v1/resource-grants", {
    method: "POST",
    body: grant,
  });
  return result.grant;
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
