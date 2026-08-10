import type { ChatTurnSnapshot } from "@/api/models";

export function mergeConversationTurns(
  ...collections: readonly ChatTurnSnapshot[][]
): ChatTurnSnapshot[] {
  const merged = new Map<string, ChatTurnSnapshot>();
  for (const turns of collections) {
    for (const turn of turns) {
      const current = merged.get(turn.turn_id);
      if (!current || turn.revision >= current.revision) merged.set(turn.turn_id, turn);
    }
  }
  return [...merged.values()].sort(
    (left, right) => left.created_at - right.created_at || left.turn_id.localeCompare(right.turn_id),
  );
}
