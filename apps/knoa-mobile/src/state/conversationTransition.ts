export function shouldResetConversation(
  previousSessionHandle: string,
  nextSessionHandle: string,
): boolean {
  return Boolean(
    previousSessionHandle
      && previousSessionHandle !== nextSessionHandle,
  );
}

export function resolveNewConversationAgent(
  requestedAgentId: string | undefined,
  availableAgentIds: readonly string[],
  defaultAgentId: string,
): string {
  if (!requestedAgentId) return defaultAgentId;
  if (!availableAgentIds.includes(requestedAgentId)) {
    throw new Error(`Agent ${requestedAgentId} 当前不可用，请刷新 Agent 状态后重试`);
  }
  return requestedAgentId;
}

export async function createProvisionalConversation(
  client: { createSession(agentId?: string): Promise<string> },
  requestedAgentId: string,
): Promise<string> {
  // POST /v1/sessions creates the runtime session, while the durable
  // conversation is materialized by the first turn.  Reading conversation
  // metadata here will therefore always return 404 and block that first turn.
  return client.createSession(requestedAgentId);
}
