/**
 * 任务执行节点推荐（纯函数，可测）。
 * 优先级：当前连接的节点 > 在线的已绑定节点 > 第一个已绑定节点。
 * Hub 在线状态未知（null）时直接沿用当前节点，不瞎推荐。
 */
export function recommendNodeId(
  boundNodeIds: string[],
  hubOnlineIds: string[] | null,
  currentNodeId: string,
): string | null {
  if (!boundNodeIds.length) return null;
  if (currentNodeId && boundNodeIds.includes(currentNodeId)) return currentNodeId;
  if (hubOnlineIds) {
    const online = boundNodeIds.find((id) => hubOnlineIds.includes(id));
    if (online) return online;
  }
  return boundNodeIds[0] ?? null;
}
