import { File, Paths } from "expo-file-system";

import type { ConversationSession } from "@/api/models";

const VERSION = 1 as const;

export type ConversationListCache = {
  sessions: ConversationSession[];
  nextCursor: string;
  updatedAt: number;
};

export async function loadConversationListCache(scope: string): Promise<ConversationListCache | null> {
  if (!scope) return null;
  const file = cacheFile(scope);
  if (!file.exists) return null;
  try {
    const value = JSON.parse(await file.text()) as Partial<ConversationListCache> & { version?: number; scope?: string };
    if (value.version !== VERSION || value.scope !== scope || !Array.isArray(value.sessions)) return null;
    if (!value.sessions.every(isConversationSession)) return null;
    return {
      sessions: value.sessions,
      nextCursor: typeof value.nextCursor === "string" ? value.nextCursor : "",
      updatedAt: typeof value.updatedAt === "number" ? value.updatedAt : 0,
    };
  } catch {
    return null;
  }
}

export async function storeConversationListCache(
  scope: string,
  sessions: ConversationSession[],
  nextCursor: string,
): Promise<void> {
  if (!scope) return;
  const file = cacheFile(scope);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({
    version: VERSION,
    scope,
    updatedAt: Date.now(),
    sessions,
    nextCursor,
  }));
}

function cacheFile(scope: string): File {
  return new File(Paths.document, `conversation-list-v${VERSION}-${hash(scope)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}

function isConversationSession(value: unknown): value is ConversationSession {
  if (!value || typeof value !== "object") return false;
  const session = value as Partial<ConversationSession>;
  return typeof session.session_handle === "string"
    && typeof session.title === "string"
    && typeof session.agent_id === "string"
    && typeof session.state === "string"
    && typeof session.revision === "number"
    && typeof session.turn_count === "number"
    && typeof session.created_at === "number";
}
