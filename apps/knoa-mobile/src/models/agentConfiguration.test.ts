import { describe, expect, it } from "vitest";

import type { ManagedConfig } from "@/api/models";
import {
  createKnoaAgent,
  normalizeAgentId,
  removeNodeAgent,
  setDelegationEnabled,
  upsertNodeAgent,
} from "./agentConfiguration";

function config(): ManagedConfig {
  const knoa = createKnoaAgent("primary", "Knoa");
  return {
    schema_version: 2,
    providers: {
      local: { driver: "llamacpp", server_url: "http://127.0.0.1:8192", api_base: "", api_key_ref: "", api_key_env: "", remote_deployment_id: "", direct_gateway_url: "", secret_version: 0, requires_api_key: false, timeout_seconds: 120 },
    },
    models: { primary: { provider: "local", model: "qwen" } },
    model_deployments: {},
    default_model: "primary",
    fallback_model: "",
    fallback_enabled: false,
    agents: { default_agent: "knoa", agents: { knoa } },
    approval_review: { mode: "off", agent_id: "knoa", timeout_seconds: 30, max_output_tokens: 1024, auto_max_risk: "low" },
    skills: {},
    mcp_servers: {},
    operational: { llm_temperature: 0.7, max_iterations: 32, max_total_tool_calls: 50, max_output_tokens: 4096, context_window_budget: 8192, task_capacity: 128, principal_task_capacity: 32, generation_drain_seconds: 120 },
  };
}

describe("agent configuration", () => {
  it("creates a complete custom Knoa Agent without adding Profile or Definition resources", () => {
    const source = config();
    const agent = createKnoaAgent("primary", "Research Agent");
    const next = upsertNodeAgent(source, "research_agent", agent);

    expect(next.agents.agents.research_agent).toMatchObject({
      kind: "knoa",
      display_name: "Research Agent",
      model_binding: { ownership: "platform", model: "primary" },
      visibility: "user",
    });
    expect(source.agents.agents.research_agent).toBeUndefined();
  });

  it("enables delegation with bounded defaults and resets every child limit when disabled", () => {
    const enabled = setDelegationEnabled(createKnoaAgent("primary"), true);
    expect(enabled.delegation).toMatchObject({ allowed: true, max_depth: 1, max_children: 3, max_parallel_children: 1, max_deadline_seconds: 1800 });
    const disabled = setDelegationEnabled(enabled, false);
    expect(disabled.delegation).toEqual({ allowed: false, targets: [], max_depth: 0, max_children: 0, max_parallel_children: 0, max_deadline_seconds: 0 });
  });

  it("removes custom agents and cleans parent delegation targets", () => {
    const source = config();
    const child = createKnoaAgent("primary", "Child");
    let next = upsertNodeAgent(source, "child", { ...child, visibility: "delegate" });
    next.agents.agents.knoa = setDelegationEnabled(next.agents.agents.knoa!, true);
    next.agents.agents.knoa!.delegation.targets = ["child"];

    next = removeNodeAgent(next, "child");
    expect(next.agents.agents.child).toBeUndefined();
    expect(next.agents.agents.knoa?.delegation.targets).toEqual([]);
    expect(() => removeNodeAgent(next, "knoa")).toThrow(/内置 Agent/);
  });

  it("cleans delegation references when a target becomes user-visible", () => {
    const source = config();
    const child = { ...createKnoaAgent("primary", "Child"), visibility: "delegate" as const };
    let next = upsertNodeAgent(source, "child", child);
    next.agents.agents.knoa = setDelegationEnabled(next.agents.agents.knoa!, true);
    next.agents.agents.knoa!.delegation.targets = ["child"];

    next = upsertNodeAgent(next, "child", { ...child, visibility: "user" }, "child");
    expect(next.agents.agents.knoa?.delegation.targets).toEqual([]);
  });

  it("normalizes suggested IDs but still rejects invalid persisted IDs", () => {
    expect(normalizeAgentId("  Research Agent 2 ")).toBe("research_agent_2");
    expect(() => upsertNodeAgent(config(), "2-agent", createKnoaAgent("primary"))).toThrow(/Agent ID/);
  });
});
