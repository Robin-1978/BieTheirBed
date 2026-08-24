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

export function requireMatchingConversationAgent(
  requestedAgentId: string,
  actualAgentId: string,
): void {
  if (requestedAgentId !== actualAgentId) {
    throw new Error(`会话 Agent 绑定不一致：请求 ${requestedAgentId}，实际 ${actualAgentId}`);
  }
}
