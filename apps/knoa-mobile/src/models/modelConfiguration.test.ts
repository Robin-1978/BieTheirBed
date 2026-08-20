import { describe, expect, it } from "vitest";

import type { ManagedConfig } from "@/api/models";
import {
  attachWorkspaceRemoteModel,
  modelAliasForRemoteDeployment,
  setModelSharing,
  upsertModel,
} from "./modelConfiguration";

function config(): ManagedConfig {
  return {
    schema_version: 2,
    providers: {
      current: {
        driver: "llamacpp",
        server_url: "http://127.0.0.1:8192",
        api_base: "",
        api_key_ref: "",
        api_key_env: "",
        remote_deployment_id: "",
        direct_gateway_url: "",
        secret_version: 0,
        requires_api_key: false,
        timeout_seconds: 120,
      },
    },
    models: { current: { provider: "current", model: "qwen" } },
    model_deployments: {},
    default_model: "current",
    vision_model: "",
    fallback_model: "",
    fallback_enabled: false,
    agents: {
      default_agent: "knoa",
      agents: {
        knoa: {
          display_name: "Knoa",
          kind: "knoa",
          enabled: true,
          instructions: "",
          instructions_ref: "",
          instructions_required: false,
          visibility: "user",
          model_binding: { ownership: "platform", model: "current", hint: "" },
          default_skill_refs: [],
          allowed_skill_refs: [],
          allowed_platform_tools: [],
          platform_capability_ceiling: [],
          native_capability_ceiling: [],
          runtime_limits: { max_iterations: null, max_output_tokens: null },
          max_concurrency: 1,
          delegation: { allowed: false, targets: [], max_depth: 0, max_children: 0, max_parallel_children: 0, max_deadline_seconds: 0 },
          callable_by: [],
          command: [],
          home: "",
          cwd: "",
          sandbox: "read-only",
          approval_policy: "never",
        },
      },
    },
    approval_review: { mode: "off", agent_id: "knoa", timeout_seconds: 30, max_output_tokens: 1024, auto_max_risk: "low" },
    skills: {},
    mcp_servers: {},
    operational: {
      llm_temperature: 0.7,
      max_iterations: 32,
      max_total_tool_calls: 50,
      max_output_tokens: 4096,
      context_window_budget: 8192,
      task_capacity: 128,
      principal_task_capacity: 32,
      generation_drain_seconds: 120,
    },
  };
}

describe("model configuration", () => {
  it("adds a model without changing default or agent bindings", () => {
    const next = upsertModel(config(), {
      alias: "new_model",
      providerId: "new_provider",
      driver: "openai_compatible",
      endpoint: "https://example.test/v1",
      modelId: "model-1",
      secretRef: "new_provider_key",
      secretVersion: 1,
      supportsVision: false,
      setAsDefault: false,
    });
    expect(next.default_model).toBe("current");
    expect(next.agents.agents.knoa?.model_binding.model).toBe("current");
    expect(next.models.new_model?.model).toBe("model-1");
  });

  it("shares only the selected model", () => {
    const next = setModelSharing(config(), "current", {
      deploymentId: "deployment-1",
      resourceId: "resource-1",
      displayName: "Qwen",
      enabled: true,
      maxRemoteConcurrency: 2,
      allowedNodeIds: ["node-b"],
    });
    expect(next.model_deployments["deployment-1"]).toMatchObject({
      model_alias: "current",
      share_enabled: true,
      max_remote_concurrency: 2,
      allowed_node_ids: ["node-b"],
    });
  });

  it("attaches a granted Workspace model without changing Agent bindings", () => {
    const next = attachWorkspaceRemoteModel(config(), {
      providerId: "remote_provider_123",
      modelAlias: "remote_model_123",
      deploymentId: "deployment-remote",
      displayName: "Company Qwen",
      modelIdentity: "qwen3.5-4b",
      supportsVision: false,
    });

    expect(next.providers.remote_provider_123).toMatchObject({
      driver: "workspace_remote",
      remote_deployment_id: "deployment-remote",
      direct_gateway_url: "",
      requires_api_key: false,
    });
    expect(next.models.remote_model_123).toMatchObject({
      provider: "remote_provider_123",
      model: "qwen3.5-4b",
    });
    expect(next.agents.agents.knoa?.model_binding.model).toBe("current");
    expect(modelAliasForRemoteDeployment(next, "deployment-remote")).toBe("remote_model_123");
  });
});
