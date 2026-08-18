import type { ManagedConfig } from "@/api/models";

export type ModelDriver = ManagedConfig["providers"][string]["driver"];

export type ModelEditorValue = {
  alias: string;
  providerId: string;
  driver: ModelDriver;
  endpoint: string;
  modelId: string;
  secretRef: string;
  secretVersion: number;
  supportsVision: boolean;
  setAsDefault: boolean;
};

export type ModelSharingValue = {
  deploymentId: string;
  resourceId: string;
  displayName: string;
  enabled: boolean;
  maxRemoteConcurrency: number;
};

export function cloneManagedConfig(document: ManagedConfig): ManagedConfig {
  return JSON.parse(JSON.stringify(document)) as ManagedConfig;
}

export function upsertModel(
  source: ManagedConfig,
  value: ModelEditorValue,
): ManagedConfig {
  const document = cloneManagedConfig(source);
  const existing = document.providers[value.providerId];
  const isLocal = value.driver === "llamacpp";
  const isRemote = value.driver === "workspace_remote";
  document.providers[value.providerId] = {
    driver: value.driver,
    server_url: isLocal ? value.endpoint.trim() : "",
    api_base: isLocal || isRemote ? "" : value.endpoint.trim(),
    api_key_ref: isLocal || isRemote ? "" : value.secretRef.trim(),
    api_key_env: "",
    remote_deployment_id: isRemote ? existing?.remote_deployment_id ?? "" : "",
    direct_gateway_url: isRemote ? value.endpoint.trim() : "",
    secret_version: Math.max(existing?.secret_version ?? 0, value.secretVersion),
    requires_api_key: isLocal || isRemote ? false : true,
    timeout_seconds: existing?.timeout_seconds ?? 120,
  };
  document.models[value.alias] = {
    ...(document.models[value.alias] ?? {}),
    provider: value.providerId,
    model: value.modelId.trim(),
    supports_vision: value.supportsVision,
  };
  if (value.setAsDefault) document.default_model = value.alias;
  return document;
}

export function setModelSharing(
  source: ManagedConfig,
  modelAlias: string,
  value: ModelSharingValue,
): ManagedConfig {
  const document = cloneManagedConfig(source);
  document.model_deployments[value.deploymentId] = {
    model_alias: modelAlias,
    resource_id: value.resourceId,
    display_name: value.displayName,
    enabled: true,
    share_enabled: value.enabled,
    max_remote_concurrency: value.maxRemoteConcurrency,
  };
  return document;
}

export function deploymentForModel(document: ManagedConfig, modelAlias: string) {
  return Object.entries(document.model_deployments).find(
    ([, deployment]) => deployment.model_alias === modelAlias,
  );
}

export function providerEndpoint(
  provider: ManagedConfig["providers"][string],
): string {
  return provider.driver === "llamacpp" ? provider.server_url : provider.api_base || provider.direct_gateway_url;
}
