import * as Crypto from "expo-crypto";

import {
  loadWorkspaceResourceState,
  putWorkspaceDeployment,
  putWorkspaceResource,
  putWorkspaceResourceGrant,
  revokeWorkspaceResourceGrant,
  type WorkspaceResourceState,
} from "@/hub/hubClient";
import type { ModelDriver } from "./modelConfiguration";

export type WorkspaceModelShare = {
  state: WorkspaceResourceState;
  nodeId: string;
  resourceId: string;
  deploymentId: string;
  displayName: string;
  modelIdentity: string;
  driver: ModelDriver;
  supportsVision: boolean;
  maxRemoteConcurrency: number;
  allowedNodeIds: string[];
  enabled: boolean;
};

export async function publishWorkspaceModelShare(input: WorkspaceModelShare): Promise<WorkspaceResourceState> {
  const spec = {
    provider_protocol: input.driver === "anthropic" ? "anthropic" : "openai_compatible",
    model_identity: input.modelIdentity,
    declared_capabilities: {
      streaming: true,
      tools: true,
      vision: input.supportsVision,
    },
  };
  const digest = await Crypto.digestStringAsync(
    Crypto.CryptoDigestAlgorithm.SHA256,
    JSON.stringify({ kind: "model", spec }),
  );
  const currentResource = input.state.workspaceResources.find(
    (item) => item.resource_id === input.resourceId,
  );
  const resourceGeneration = currentResource
    ? currentResource.canonical_digest === digest
      ? currentResource.generation
      : currentResource.generation + 1
    : 1;
  await putWorkspaceResource({
    resource_id: input.resourceId,
    kind: "model",
    generation: resourceGeneration,
    canonical_digest: digest,
    display_name: input.displayName,
    spec,
    enabled: input.enabled,
  });
  const currentDeployment = input.state.workspaceDeployments.find(
    (item) => item.deployment_id === input.deploymentId,
  );
  await putWorkspaceDeployment({
    deployment_id: input.deploymentId,
    kind: "model",
    resource_id: input.resourceId,
    resource_generation: resourceGeneration,
    resource_digest: digest,
    target_node_id: input.nodeId,
    desired_generation: (currentDeployment?.desired_generation ?? 0) + 1,
    spec: { max_remote_concurrency: input.maxRemoteConcurrency },
    enabled: input.enabled,
  });

  const allowed = new Set(input.enabled ? input.allowedNodeIds : []);
  const existing = input.state.grants.filter(
    (grant) => grant.target_deployment_id === input.deploymentId && grant.revoked_at === null,
  );
  await Promise.all(existing
    .filter((grant) => !allowed.has(grant.caller_node_id))
    .map((grant) => revokeWorkspaceResourceGrant(grant.grant_id)));
  for (const callerNodeId of allowed) {
    const material = `${input.deploymentId}:${callerNodeId}:model_inference`;
    const grantDigest = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, material);
    await putWorkspaceResourceGrant({
      grant_id: `grant_${grantDigest.slice(0, 40)}`,
      caller_node_id: callerNodeId,
      target_deployment_id: input.deploymentId,
      capability: "model_inference",
      max_request_deadline: 600,
      expires_at: Date.now() / 1000 + 365 * 24 * 60 * 60,
    });
  }
  return loadWorkspaceResourceState();
}
