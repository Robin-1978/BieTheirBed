import type { CodingSessionMessage, CodingSessionSummary } from "../api/models";

export type CodingSessionKey = Pick<CodingSessionSummary, "provider" | "external_session_id">;

export type CodingSessionAction = "open" | "attach" | "prompt" | "interrupt";

export type CodingSessionListSnapshot = {
  sessions: CodingSessionSummary[];
  error: string;
};

/**
 * The interrupt button follows what the provider declares: a running session
 * that another client owns reports no `interrupt`, and an idle one has nothing
 * to interrupt.
 */
export function canInterruptCodingSession(session: Pick<CodingSessionSummary, "capabilities">): boolean {
  return session.capabilities.includes("interrupt");
}

/** The gateway answers `interrupted: false` when there was nothing to interrupt. */
export function interruptOutcomeMessage(interrupted: boolean): string {
  return interrupted ? "" : "没有正在运行、且可由本服务中断的任务，已刷新会话状态。";
}

/**
 * Lists the sessions.  A provider that is down only matters while nothing could
 * be listed at all: with the other provider's sessions on screen the list is
 * still useful.  A failed listing rejects so the caller can report it.
 */
export async function loadCodingSessionList(input: {
  list(): Promise<{ sessions: CodingSessionSummary[]; providerErrors: Array<{ provider: string; error: string }> }>;
}): Promise<CodingSessionListSnapshot> {
  const listing = await input.list();
  const error = listing.providerErrors.length && !listing.sessions.length
    ? listing.providerErrors.map((item) => `${item.provider}: ${item.error}`).join("\n")
    : "";
  return { sessions: listing.sessions, error };
}

export function codingSessionStateLabel(state: string): string {
  if (state === "running") return "运行中 · 只读";
  if (state === "interrupted") return "已中断";
  if (state === "failed") return "失败";
  if (state === "idle") return "空闲，可继续";
  return "未知";
}

export function codingMessageRoleLabel(role: string): string {
  if (role === "user") return "你";
  if (role === "assistant") return "助手";
  return role;
}

function firstParam(value: string | string[] | undefined): string {
  const raw = Array.isArray(value) ? value[0] : value;
  return typeof raw === "string" ? raw.trim() : "";
}

/**
 * Reads the session a detail screen was opened for from the router params
 * (`useLocalSearchParams` may hand over an array).  Null when either part is
 * missing, so the screen refuses to load instead of asking for "undefined".
 */
export function codingSessionRouteKey(params: {
  provider?: string | string[];
  id?: string | string[];
}): CodingSessionKey | null {
  const provider = firstParam(params.provider);
  const externalSessionId = firstParam(params.id);
  if (!provider || !externalSessionId) return null;
  return { provider, external_session_id: externalSessionId };
}

/**
 * A message can come back without text: the Codex bridge keeps only the
 * userMessage/agentMessage items and the OpenCode bridge only the `text` parts
 * of an assistant row, so a message made of anything else has an empty `text`.
 * Shown as empty cards they bury the conversation, so only messages with text
 * are listed; the number left out is returned so the screen can say so.  What
 * the gateway returns is kept whole (it already caps it at the last 200): the
 * detail screen scrolls instead of cutting the history to the last few entries.
 */
export function readableCodingMessages(messages: readonly CodingSessionMessage[]): {
  messages: CodingSessionMessage[];
  hidden: number;
} {
  const readable = messages.filter((message) => message.text.trim().length > 0);
  return { messages: readable, hidden: messages.length - readable.length };
}

const FORBIDDEN = "没有权限使用这个编码会话：只有这台电脑的所有者可以操作，且会话的工作目录必须在允许范围内。";

const CONFLICT: Partial<Record<CodingSessionAction, string>> = {
  attach: "绑定冲突：这个编码会话已归属于另一个小诺会话，或当前小诺会话已绑定了别的编码会话。",
  prompt: "暂时无法继续：请先绑定当前小诺会话，并确认桌面端没有在运行这个会话。",
  interrupt: "暂时无法中断：请先绑定当前小诺会话，并确认该会话有可由本服务中断的运行任务。",
};

/**
 * The gateway answers 403 for a non-owner principal or a directory outside the
 * allow list, and 409 for binding/ownership conflicts; the generic client text
 * for those statuses ("请求未完成，请检查输入后重试") does not help the user.
 */
export function codingSessionErrorMessage(
  cause: unknown,
  action: CodingSessionAction,
  fallback: string,
): string {
  const status = typeof cause === "object" && cause !== null
    ? (cause as { status?: unknown }).status
    : undefined;
  if (status === 403) return FORBIDDEN;
  if (status === 409) {
    const message = CONFLICT[action];
    if (message) return message;
  }
  return cause instanceof Error && cause.message ? cause.message : fallback;
}
