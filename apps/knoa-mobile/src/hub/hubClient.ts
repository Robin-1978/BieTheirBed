import * as SecureStore from "expo-secure-store";

import type { AndroidRelease } from "@/api/models";
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
  rootUrl: string;
  token: string;
  accountId: string;
  hubId: string;
  workspaceId: string;
  identityIssuerId: string;
  signingPublicKey: string;
  deploymentMode: "self_hosted" | "hosted_single_node" | "hosted";
};

export type HostedWorkspace = {
  workspaceId: string;
  displayName: string;
  kind: "personal" | "shared";
  role: "owner" | "admin" | "member";
  workspacePath: string;
};

export type HostedWorkspaceMember = {
  accountId: string;
  loginIdentity: string;
  displayName: string;
  role: "owner" | "admin" | "member";
  createdAt: number;
};

export type HostedAccountSession = {
  accountId: string;
  loginIdentity: string;
  expiresAt: number;
  workspaces: HostedWorkspace[];
  connection: HubConnection;
};

export type HostedAccountProfile = {
  accountId: string;
  loginIdentity: string;
  displayName: string;
  expiresAt: number;
  workspaces: HostedWorkspace[];
};

type HostedAccountResponse = {
  account_id: string;
  login_identity: string;
  access_token: string;
  expires_at: number;
  workspace_id: string;
  workspace_path: string;
  workspaces: Array<{
    workspace_id: string;
    display_name: string;
    kind: "personal" | "shared";
    role: "owner" | "admin" | "member";
    workspace_path: string;
  }>;
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

export async function connectHub(
  url: string,
  token: string,
  displayName: string,
  hosted: { rootUrl: string; accountId: string } | null = null,
): Promise<HubConnection> {
  const normalized = url.trim().replace(/\/$/, "");
  if (!/^https?:\/\//.test(normalized) || token.length < 32) throw new Error("Hub 地址或帐号令牌无效");
  const hub = await request<{
    hub_id: string;
    workspace_id: string;
    identity_issuer_id: string;
    signing_public_key: string;
    deployment_mode: "self_hosted" | "hosted_single_node" | "hosted";
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
    rootUrl: hosted?.rootUrl ?? normalized,
    token,
    accountId: hosted?.accountId ?? "",
    hubId: hub.hub_id,
    workspaceId: hub.workspace_id,
    identityIssuerId: hub.identity_issuer_id,
    signingPublicKey: hub.signing_public_key,
    deploymentMode: hub.deployment_mode,
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
      rootUrl: parsed.rootUrl || parsed.url,
      accountId: parsed.accountId || "",
      workspaceId: parsed.workspaceId || parsed.hubId,
      identityIssuerId: parsed.identityIssuerId || parsed.hubId,
      deploymentMode: parsed.deploymentMode || "self_hosted",
    };
  } catch {
    return null;
  }
}

export async function loadHostedAccount(): Promise<HostedAccountProfile | null> {
  const connection = await requiredHubConnection();
  if (!connection.accountId) return null;
  const account = await request<{
    account_id: string;
    login_identity: string;
    display_name: string;
    expires_at: number;
    workspaces: HostedAccountResponse["workspaces"];
  }>(connection.rootUrl, connection.token, "/v1/hosted/account");
  return {
    accountId: account.account_id,
    loginIdentity: account.login_identity,
    displayName: account.display_name,
    expiresAt: account.expires_at,
    workspaces: account.workspaces.map(hostedWorkspace),
  };
}

export async function logoutHostedAccount(): Promise<void> {
  const connection = await loadHubConnection();
  try {
    if (connection?.accountId) {
      await request(connection.rootUrl, connection.token, "/v1/hosted/session", { method: "DELETE" });
    }
  } finally {
    await SecureStore.deleteItemAsync(HUB_CONNECTION);
  }
}

export async function resolveAndroidRelease(
  nodeRelease: () => Promise<AndroidRelease>,
): Promise<AndroidRelease | null> {
  const connection = await loadHubConnection();
  if (!connection?.accountId) return nodeRelease();
  const response = await fetch(
    `${connection.rootUrl}/v1/mobile/releases/android/latest`,
    { headers: { Authorization: `Bearer ${connection.token}` } },
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Hub 帐号认证失败" : "Hub App 更新检查失败");
  }
  const release = await response.json() as AndroidRelease;
  return {
    ...release,
    download_path: new URL(release.download_path, `${connection.rootUrl}/`).toString(),
  };
}

export async function registerHostedAccount(
  setupPayload: string,
  loginIdentity: string,
  displayName: string,
  password: string,
): Promise<HostedAccountSession> {
  const setup = parseHostedSetupPayload(setupPayload);
  const account = await request<HostedAccountResponse>(setup.hubUrl, "", "/v1/hosted/accounts", {
    method: "POST",
    body: {
      grant_id: setup.grantId,
      grant_secret: setup.grantSecret,
      login_identity: loginIdentity.trim(),
      display_name: displayName.trim() || "Knoa User",
      password,
    },
  });
  return connectHostedAccount(setup.hubUrl, account);
}

export async function loginHostedAccount(
  url: string,
  loginIdentity: string,
  password: string,
): Promise<HostedAccountSession> {
  const normalized = hostedRootUrl(url);
  const account = await request<HostedAccountResponse>(normalized, "", "/v1/hosted/sessions", {
    method: "POST",
    body: { login_identity: loginIdentity.trim(), password },
  });
  return connectHostedAccount(normalized, account);
}

export async function resetHostedPassword(
  setupPayload: string,
  newPassword: string,
): Promise<HostedAccountSession> {
  const setup = parseHostedGrantPayload(setupPayload, "knoa-hosted-password-reset-v1");
  const account = await request<HostedAccountResponse>(setup.hubUrl, "", "/v1/hosted/password-reset", {
    method: "POST",
    body: {
      grant_id: setup.grantId,
      grant_secret: setup.grantSecret,
      new_password: newPassword,
    },
  });
  return connectHostedAccount(setup.hubUrl, account);
}

export async function listHostedWorkspaces(): Promise<HostedWorkspace[]> {
  const connection = await requiredHubConnection();
  if (!connection.accountId) return [];
  const result = await request<{ workspaces: HostedAccountResponse["workspaces"] }>(
    connection.rootUrl,
    connection.token,
    "/v1/hosted/workspaces",
  );
  return result.workspaces.map(hostedWorkspace);
}

export async function createHostedWorkspace(displayName: string): Promise<HostedWorkspace> {
  const connection = await requiredHubConnection();
  if (!connection.accountId) throw new Error("当前连接不是 Hosted Account");
  const result = await request<HostedAccountResponse["workspaces"][number]>(
    connection.rootUrl,
    connection.token,
    "/v1/hosted/workspaces",
    { method: "POST", body: { display_name: displayName.trim(), kind: "shared" } },
  );
  return hostedWorkspace(result);
}

export async function listHostedWorkspaceMembers(
  workspaceId: string,
): Promise<HostedWorkspaceMember[]> {
  const connection = await requiredHubConnection();
  const result = await request<{
    members: Array<{
      account_id: string;
      login_identity: string;
      display_name: string;
      role: "owner" | "admin" | "member";
      created_at: number;
    }>;
  }>(
    connection.rootUrl,
    connection.token,
    `/v1/hosted/workspaces/${encodeURIComponent(workspaceId)}/members`,
  );
  return result.members.map((member) => ({
    accountId: member.account_id,
    loginIdentity: member.login_identity,
    displayName: member.display_name,
    role: member.role,
    createdAt: member.created_at,
  }));
}

export async function addHostedWorkspaceMember(
  workspaceId: string,
  loginIdentity: string,
  role: "admin" | "member" = "member",
): Promise<HostedWorkspaceMember> {
  const connection = await requiredHubConnection();
  const member = await request<{
    account_id: string;
    login_identity: string;
    display_name: string;
    role: "owner" | "admin" | "member";
    created_at: number;
  }>(
    connection.rootUrl,
    connection.token,
    `/v1/hosted/workspaces/${encodeURIComponent(workspaceId)}/members`,
    { method: "POST", body: { login_identity: loginIdentity.trim(), role } },
  );
  return {
    accountId: member.account_id,
    loginIdentity: member.login_identity,
    displayName: member.display_name,
    role: member.role,
    createdAt: member.created_at,
  };
}

export async function removeHostedWorkspaceMember(
  workspaceId: string,
  accountId: string,
): Promise<void> {
  const connection = await requiredHubConnection();
  await request(
    connection.rootUrl,
    connection.token,
    `/v1/hosted/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(accountId)}`,
    { method: "DELETE" },
  );
}

export async function selectHostedWorkspace(workspace: HostedWorkspace): Promise<HubConnection> {
  const current = await requiredHubConnection();
  if (!current.accountId) throw new Error("当前连接不是 Hosted Account");
  return connectHub(
    `${current.rootUrl}${workspace.workspacePath}`,
    current.token,
    "Knoa Mobile",
    { rootUrl: current.rootUrl, accountId: current.accountId },
  );
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
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) throw new Error(response.status === 401 ? "Hub 帐号认证失败" : "Hub 请求失败");
  return response.json() as Promise<T>;
}

async function connectHostedAccount(
  rootUrl: string,
  account: HostedAccountResponse,
): Promise<HostedAccountSession> {
  const connection = await connectHub(
    `${rootUrl}${account.workspace_path}`,
    account.access_token,
    "Knoa Mobile",
    { rootUrl, accountId: account.account_id },
  );
  return {
    accountId: account.account_id,
    loginIdentity: account.login_identity,
    expiresAt: account.expires_at,
    workspaces: account.workspaces.map(hostedWorkspace),
    connection,
  };
}

function hostedWorkspace(value: HostedAccountResponse["workspaces"][number]): HostedWorkspace {
  return {
    workspaceId: value.workspace_id,
    displayName: value.display_name,
    kind: value.kind,
    role: value.role,
    workspacePath: value.workspace_path,
  };
}

function hostedRootUrl(value: string): string {
  const normalized = value.trim().replace(/\/$/, "");
  if (!/^https?:\/\//.test(normalized)) throw new Error("Hosted Hub 地址无效");
  return normalized;
}

function parseHostedSetupPayload(value: string): {
  hubUrl: string;
  grantId: string;
  grantSecret: string;
} {
  return parseHostedGrantPayload(value, "knoa-hosted-account-v1");
}

function parseHostedGrantPayload(value: string, version: string): {
  hubUrl: string;
  grantId: string;
  grantSecret: string;
} {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(value.trim()) as Record<string, unknown>;
  } catch {
    throw new Error("Hosted 注册凭证无效");
  }
  if (
    parsed.version !== version
    || typeof parsed.hub_url !== "string"
    || typeof parsed.grant_id !== "string"
    || typeof parsed.grant_secret !== "string"
    || typeof parsed.expires_at !== "number"
    || parsed.expires_at <= Date.now() / 1000
  ) throw new Error("Hosted 注册凭证无效或已过期");
  return {
    hubUrl: hostedRootUrl(parsed.hub_url),
    grantId: parsed.grant_id,
    grantSecret: parsed.grant_secret,
  };
}
