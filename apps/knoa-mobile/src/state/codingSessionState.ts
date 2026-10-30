import type { CodingSessionDetail, CodingSessionSummary } from "../api/models";

export type CodingSessionKey = Pick<CodingSessionSummary, "provider" | "external_session_id">;

export type CodingSessionAction = "open" | "attach" | "prompt" | "interrupt";

export type CodingSessionSnapshot = {
  sessions: CodingSessionSummary[];
  /** Fresh detail of the selected session; null when nothing is selected or the selection moved on. */
  detail: CodingSessionDetail | null;
  error: string;
};

export function sameCodingSession(
  left: CodingSessionKey | null | undefined,
  right: CodingSessionKey | null | undefined,
): boolean {
  return Boolean(
    left
      && right
      && left.provider === right.provider
      && left.external_session_id === right.external_session_id,
  );
}

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
 * Lists the sessions and refreshes the selected one.
 *
 * `selectedKey` is read again after the detail request: the user may have
 * closed the panel or opened another session meanwhile, and a late response
 * must not reopen or overwrite what is on screen now.  A failed listing
 * rejects; a failed detail request is reported in `error` so the freshly
 * listed sessions are still shown.
 */
export async function loadCodingSessionSnapshot(input: {
  list(): Promise<{ sessions: CodingSessionSummary[]; providerErrors: Array<{ provider: string; error: string }> }>;
  getDetail(key: CodingSessionKey): Promise<CodingSessionDetail>;
  selectedKey(): CodingSessionKey | null;
}): Promise<CodingSessionSnapshot> {
  const listing = await input.list();
  let detail: CodingSessionDetail | null = null;
  let error = listing.providerErrors.length && !listing.sessions.length
    ? listing.providerErrors.map((item) => `${item.provider}: ${item.error}`).join("\n")
    : "";
  const requested = input.selectedKey();
  if (requested) {
    try {
      const fetched = await input.getDetail(requested);
      if (sameCodingSession(input.selectedKey(), requested)) detail = fetched;
    } catch (cause) {
      if (sameCodingSession(input.selectedKey(), requested)) {
        error = codingSessionErrorMessage(cause, "open", "无法读取会话详情");
      }
    }
  }
  return { sessions: listing.sessions, detail, error };
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
