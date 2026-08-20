import type { ManagedConfig } from "@/api/models";

export type AgentImageSupport = {
  supported: boolean;
  modelAlias: string;
};

export function agentImageSupport(
  config: ManagedConfig,
  agentId: string,
): AgentImageSupport {
  const agent = config.agents.agents[agentId];
  if (!agent || agent.model_binding.ownership !== "platform") {
    return { supported: true, modelAlias: "" };
  }
  const modelAlias = agent.model_binding.model || config.default_model;
  const dedicatedVision = config.vision_model && config.models[config.vision_model]?.supports_vision === true;
  return {
    supported: config.models[modelAlias]?.supports_vision === true || Boolean(dedicatedVision),
    modelAlias: dedicatedVision ? config.vision_model : modelAlias,
  };
}
