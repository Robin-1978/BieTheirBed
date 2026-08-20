import { describe, expect, it } from "vitest";

import type { ManagedConfig } from "@/api/models";
import { agentImageSupport } from "./agentImageSupport";

function config(supportsVision: boolean): ManagedConfig {
  return {
    schema_version: 2,
    providers: {},
    models: { primary: { provider: "provider", model: "model", supports_vision: supportsVision } },
    model_deployments: {},
    default_model: "primary",
    vision_model: "",
    fallback_model: "",
    fallback_enabled: false,
    agents: {
      default_agent: "knoa",
      agents: {
        knoa: {
          kind: "knoa",
          display_name: "Knoa",
          instructions: "",
          instructions_ref: "builtin://assistant",
          instructions_required: true,
          visibility: "user",
          enabled: true,
          model_binding: { ownership: "platform", model: "primary", hint: "" },
          max_concurrency: 1,
          default_skill_refs: [],
          allowed_skill_refs: [],
          allowed_platform_tools: [],
          platform_capability_ceiling: [],
          native_capability_ceiling: [],
          runtime_limits: { max_iterations: null, max_output_tokens: null },
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
    approval_review: { mode: "off", agent_id: "", timeout_seconds: 60, max_output_tokens: 4096, auto_max_risk: "medium" },
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

describe("agentImageSupport", () => {
  it("accepts images only when the bound platform model explicitly supports vision", () => {
    expect(agentImageSupport(config(true), "knoa")).toEqual({ supported: true, modelAlias: "primary" });
    expect(agentImageSupport(config(false), "knoa")).toEqual({ supported: false, modelAlias: "primary" });
  });

  it("does not guess capabilities for runtime-owned agents", () => {
    const value = config(false);
    value.agents.agents.knoa!.model_binding.ownership = "runtime";
    expect(agentImageSupport(value, "knoa")).toEqual({ supported: true, modelAlias: "" });
  });

  it("accepts a text main model when a dedicated vision model is configured", () => {
    const value = config(false);
    value.models.vision = { provider: "provider", model: "vision", supports_vision: true };
    value.vision_model = "vision";
    expect(agentImageSupport(value, "knoa")).toEqual({ supported: true, modelAlias: "vision" });
  });
});
