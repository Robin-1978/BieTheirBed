import { describe, expect, it } from "vitest";

import type { ManagedConfig } from "@/api/models";
import type { HubNode, WorkspaceResourceState } from "@/hub/hubClient";
import { availableWorkspaceModels } from "./workspaceModelConsumption";

const document = {
  providers: {},
  models: {},
} as unknown as ManagedConfig;

const nodes: HubNode[] = [{
  node_id: "node-b",
  display_name: "Node B",
  signing_public_key: "signing",
  configuration_public_key: "configuration",
  platform: "windows",
  version: "1",
  direct_gateway_url: "https://node-b.example.test",
  online: true,
  last_seen: 100,
}];

const state: WorkspaceResourceState = {
  workspaceResources: [{
    resource_id: "resource-qwen",
    workspace_id: "workspace",
    kind: "model",
    generation: 1,
    canonical_digest: "a".repeat(64),
    display_name: "Qwen 3.5 4B",
    spec: { model_identity: "qwen3.5-4b" },
    enabled: true,
    created_by: "owner",
    created_at: 1,
    updated_at: 1,
  }],
  workspaceDeployments: [{
    deployment_id: "deployment-qwen",
    workspace_id: "workspace",
    kind: "model",
    resource_id: "resource-qwen",
    resource_generation: 1,
    resource_digest: "a".repeat(64),
    target_node_id: "node-b",
    desired_generation: 1,
    spec: {},
    enabled: true,
    created_at: 1,
    updated_at: 1,
  }],
  grants: [{
    grant_id: "grant-a",
    caller_node_id: "node-a",
    target_deployment_id: "deployment-qwen",
    capability: "model_inference",
    max_request_deadline: 600,
    expires_at: 200,
    revoked_at: null,
  }],
  observations: [{
    deployment_id: "deployment-qwen",
    node_id: "node-b",
    applied_digest: "b".repeat(64),
    health: "healthy",
    available_capacity: 1,
    observed_at: 100,
    expires_at: 160,
  }],
};

describe("Workspace model consumption", () => {
  it("shows only models actively granted to the caller Node", () => {
    const available = availableWorkspaceModels(document, state, nodes, "node-a", 150);

    expect(available).toHaveLength(1);
    expect(available[0]).toMatchObject({
      health: "healthy",
      attachedAlias: "",
      providerNode: { node_id: "node-b" },
      resource: { display_name: "Qwen 3.5 4B" },
    });
    expect(availableWorkspaceModels(document, state, nodes, "node-c", 150)).toEqual([]);
    expect(availableWorkspaceModels(document, state, nodes, "node-a", 201)).toEqual([]);
  });
});
