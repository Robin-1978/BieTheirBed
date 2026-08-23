/**
 * User-facing Node labels.  Node IDs are stable protocol identifiers, not
 * names a person can recognise, so they must never be used as a UI fallback.
 */
export function presentNodeName(
  node: { nodeId?: string; displayName?: string } | null | undefined,
  fallback: string,
): string {
  const name = node?.displayName?.trim();
  if (!name || (node?.nodeId && name === node.nodeId)) return fallback;
  return name;
}

export function presentHubNodeName(
  node: { node_id?: string; display_name?: string } | null | undefined,
  fallback: string,
): string {
  const name = node?.display_name?.trim();
  if (!name || (node?.node_id && name === node.node_id)) return fallback;
  return name;
}
