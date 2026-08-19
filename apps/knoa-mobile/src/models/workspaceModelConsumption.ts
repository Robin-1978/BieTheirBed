import type { ManagedConfig } from "@/api/models";
import type {
  HubNode,
  WorkspaceDeployment,
  WorkspaceResource,
  WorkspaceResourceState,
} from "@/hub/hubClient";
import { modelAliasForRemoteDeployment } from "./modelConfiguration";

export type AvailableWorkspaceModel = {
  resource: WorkspaceResource;
  deployment: WorkspaceDeployment;
  providerNode: HubNode | undefined;
  health: "healthy" | "degraded" | "unavailable" | "unknown";
  attachedAlias: string;
};

export function availableWorkspaceModels(
  document: ManagedConfig,
  state: WorkspaceResourceState,
  nodes: HubNode[],
  callerNodeId: string,
  now = Date.now() / 1000,
): AvailableWorkspaceModel[] {
  return state.workspaceDeployments.flatMap((deployment) => {
    if (!deployment.enabled || deployment.kind !== "model" || deployment.target_node_id === callerNodeId) return [];
    const authorized = state.grants.some((grant) => (
      grant.target_deployment_id === deployment.deployment_id
      && grant.caller_node_id === callerNodeId
      && grant.capability === "model_inference"
      && grant.revoked_at === null
      && grant.expires_at > now
    ));
    if (!authorized) return [];
    const resource = state.workspaceResources.find((item) => (
      item.resource_id === deployment.resource_id && item.kind === "model" && item.enabled
    ));
    if (!resource) return [];
    const observation = state.observations.find((item) => item.deployment_id === deployment.deployment_id);
    const health: AvailableWorkspaceModel["health"] = observation?.health ?? "unknown";
    return [{
      resource,
      deployment,
      providerNode: nodes.find((node) => node.node_id === deployment.target_node_id),
      health,
      attachedAlias: modelAliasForRemoteDeployment(document, deployment.deployment_id),
    }];
  }).sort((left, right) => left.resource.display_name.localeCompare(right.resource.display_name));
}

export function workspaceModelIdentity(resource: WorkspaceResource): string {
  const identity = resource.spec.model_identity;
  return typeof identity === "string" && identity.trim() ? identity.trim() : resource.display_name;
}

export function workspaceModelSupportsVision(resource: WorkspaceResource): boolean {
  const capabilities = resource.spec.declared_capabilities;
  return Boolean(
    capabilities
    && typeof capabilities === "object"
    && "vision" in capabilities
    && capabilities.vision,
  );
}
