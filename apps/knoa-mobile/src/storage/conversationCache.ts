import { File, Paths } from "expo-file-system";

import type { ChatTurnSnapshot } from "@/api/models";

type StoredConversation = {
  version: 1;
  sessionHandle: string;
  updatedAt: number;
  turns: ChatTurnSnapshot[];
};

const MAX_CACHED_TURNS = 200;

export async function loadConversationCache(sessionHandle: string): Promise<ChatTurnSnapshot[]> {
  if (!sessionHandle) return [];
  const file = cacheFile(sessionHandle);
  if (!file.exists) return [];
  try {
    const value = JSON.parse(await file.text()) as Partial<StoredConversation>;
    if (value.version !== 1 || value.sessionHandle !== sessionHandle || !Array.isArray(value.turns)) {
      return [];
    }
    return value.turns.filter(isTurn).slice(-MAX_CACHED_TURNS);
  } catch {
    return [];
  }
}

export async function storeConversationCache(
  sessionHandle: string,
  turns: ChatTurnSnapshot[],
): Promise<void> {
  if (!sessionHandle) return;
  const file = cacheFile(sessionHandle);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({
    version: 1,
    sessionHandle,
    updatedAt: Date.now(),
    turns: turns.slice(-MAX_CACHED_TURNS),
  } satisfies StoredConversation));
}

export function removeConversationCache(sessionHandle: string): void {
  if (!sessionHandle) return;
  const file = cacheFile(sessionHandle);
  if (file.exists) file.delete();
}

function cacheFile(sessionHandle: string): File {
  return new File(Paths.document, `conversation-v1-${hash(sessionHandle)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}

function isTurn(value: unknown): value is ChatTurnSnapshot {
  if (!value || typeof value !== "object") return false;
  const turn = value as Partial<ChatTurnSnapshot>;
  return typeof turn.turn_id === "string"
    && typeof turn.session_handle === "string"
    && typeof turn.created_at === "number"
    && typeof turn.revision === "number";
}
