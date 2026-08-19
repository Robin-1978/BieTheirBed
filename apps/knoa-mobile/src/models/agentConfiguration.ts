import type { ManagedConfig, ManagedNodeAgent } from "@/api/models";

import { cloneManagedConfig } from "./modelConfiguration";

export const BUILT_IN_AGENT_IDS = new Set(["knoa", "reviewer_agent", "codex"]);

const AGENT_ID_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;

export function normalizeAgentId(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^[_0-9-]+/, "").slice(0, 64);
}

export function validateAgentId(value: string): string {
  const agentId = value.trim();
  if (!AGENT_ID_PATTERN.test(agentId)) {
    throw new Error("Agent ID 必须以小写字母开头，只能包含小写字母、数字、_ 和 -");
  }
  return agentId;
}

export function createKnoaAgent(modelAlias: string, displayName = "New Knoa Agent"): ManagedNodeAgent {
  if (!modelAlias) throw new Error("创建 Agent 前需要先配置一个模型");
  return {
    kind: "knoa",
    display_name: displayName,
    instructions: "You are a Knoa Agent. Follow the user's objective and the configured policies.",
    instructions_ref: "",
    instructions_required: true,
    visibility: "user",
    enabled: true,
    model_binding: { ownership: "platform", model: modelAlias, hint: "" },
    max_concurrency: 1,
    default_skill_refs: [],
    allowed_skill_refs: [],
    allowed_platform_tools: ["*"],
    platform_capability_ceiling: ["*"],
    native_capability_ceiling: [],
    runtime_limits: { max_iterations: null, max_output_tokens: null },
    delegation: {
      allowed: false,
      targets: [],
      max_depth: 0,
      max_children: 0,
      max_parallel_children: 0,
      max_deadline_seconds: 0,
    },
    callable_by: [],
    command: [],
    home: "",
    cwd: "",
    sandbox: "read-only",
    approval_policy: "never",
    request_timeout_seconds: 120,
    max_line_bytes: 4 * 1024 * 1024,
    max_event_queue: 1024,
  };
}

export function upsertNodeAgent(
  source: ManagedConfig,
  agentIdInput: string,
  agent: ManagedNodeAgent,
  originalAgentId = "",
): ManagedConfig {
  const agentId = validateAgentId(agentIdInput);
  const original = originalAgentId.trim();
  if (original && original !== agentId) {
    throw new Error("Agent ID 创建后不可修改；可以修改显示名称");
  }
  if (!original && source.agents.agents[agentId]) {
    throw new Error("这个 Agent ID 已存在");
  }
  if (agent.kind === "knoa" && !source.models[agent.model_binding.model]) {
    throw new Error("Agent 引用的模型不存在");
  }
  if (agent.kind === "knoa" && !agent.instructions.trim() && !agent.instructions_ref.trim()) {
    throw new Error("Agent 必须配置 Prompt 或内置 Prompt 引用");
  }
  const document = cloneManagedConfig(source);
  document.agents.agents[agentId] = JSON.parse(JSON.stringify(agent)) as ManagedNodeAgent;
  if (agent.visibility !== "delegate") {
    for (const configured of Object.values(document.agents.agents)) {
      configured.delegation.targets = configured.delegation.targets.filter((target) => target !== agentId);
    }
  }
  return document;
}

export function removeNodeAgent(source: ManagedConfig, agentId: string): ManagedConfig {
  if (BUILT_IN_AGENT_IDS.has(agentId)) throw new Error("内置 Agent 不能删除，只能停用");
  if (!source.agents.agents[agentId]) throw new Error("Agent 不存在");
  if (source.agents.default_agent === agentId) throw new Error("默认 Agent 不能删除，请先切换默认 Agent");

  const document = cloneManagedConfig(source);
  delete document.agents.agents[agentId];
  for (const agent of Object.values(document.agents.agents)) {
    if (!agent.delegation.targets.includes(agentId)) continue;
    agent.delegation.targets = agent.delegation.targets.filter((target) => target !== agentId);
  }
  return document;
}

export function setDelegationEnabled(agent: ManagedNodeAgent, enabled: boolean): ManagedNodeAgent {
  const next = JSON.parse(JSON.stringify(agent)) as ManagedNodeAgent;
  next.delegation = enabled
    ? {
        allowed: true,
        targets: next.delegation.targets,
        max_depth: Math.max(1, next.delegation.max_depth || 1),
        max_children: Math.max(1, next.delegation.max_children || 3),
        max_parallel_children: Math.max(1, Math.min(next.delegation.max_parallel_children || 1, next.delegation.max_children || 3)),
        max_deadline_seconds: Math.max(1, next.delegation.max_deadline_seconds || 1800),
      }
    : {
        allowed: false,
        targets: [],
        max_depth: 0,
        max_children: 0,
        max_parallel_children: 0,
        max_deadline_seconds: 0,
      };
  return next;
}

export function csvValues(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}
