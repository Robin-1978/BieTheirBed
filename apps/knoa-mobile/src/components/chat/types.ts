import type { ArtifactInput, ChatTurnSnapshot } from "@/api/models";
import type { useI18n } from "@/i18n";

export type PendingAttachment = {
  uri: string;
  name: string;
  mediaType: string;
  status?: "pending" | "uploading" | "uploaded" | "failed";
  uploaded?: ArtifactInput;
};

export type InputMode = "text" | "voice";

export type PendingChatTurn = {
  localId: string;
  requestId: string;
  userInput: string;
  attachments: PendingAttachment[];
  state: "sending" | "failed";
  error: string;
  /** false 时重试不可能成功（如 Node 拒绝请求），UI 不再提供重试入口。 */
  retryable: boolean;
  createdAt: number;
};

export type ChatListItem =
  | { kind: "turn"; key: string; turn: ChatTurnSnapshot; showTimestamp: boolean; timestampMs: number }
  | { kind: "pending"; key: string; pending: PendingChatTurn; showTimestamp: boolean; timestampMs: number };

export type Feedback = {
  text: string;
  tone: "success" | "error" | "info" | "warning";
};

export const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);
export const TIMESTAMP_GROUP_MS = 5 * 60 * 1000;

export function attachmentStatusLabel(status: NonNullable<PendingAttachment["status"]>, t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    pending: t("chat.uploadPending"),
    uploading: t("chat.uploading"),
    uploaded: t("chat.uploaded"),
    failed: t("chat.uploadRetry"),
  })[status];
}

export function agentReasonLabel(reason: string, t: ReturnType<typeof useI18n>["t"]): string {
  if (reason === "runtime_unavailable") return t("agent.unavailableRuntime");
  if (reason === "delegate_only") return t("agent.unavailableDelegate");
  if (reason === "system_only") return t("agent.unavailableSystem");
  return t("agent.unavailableDisabled");
}

/**
 * 把发送失败的原始错误转成面向用户的文案 + 是否值得重试。
 * 400/422（Node 拒绝请求，多为 Node 版本太旧不认新协议字段）和 404
 * （会话不在该 Node 上）重试不可能成功：直接说明原因并不再提供重试。
 */
export function describeSendError(
  error: unknown,
  t: ReturnType<typeof useI18n>["t"],
): { message: string; retryable: boolean } {
  // 结构化匹配 GatewayError（status/code），避免值导入网关客户端。
  const gateway = asGatewayError(error);
  if (gateway) {
    if (gateway.status === 400 || gateway.status === 422) {
      return { message: t("chat.sendRejectedVersion"), retryable: false };
    }
    if (gateway.status === 404) {
      return { message: gateway.message, retryable: false };
    }
    return { message: gateway.message || t("chat.sendFailed"), retryable: true };
  }
  return { message: t("chat.sendFailed"), retryable: true };
}

function asGatewayError(error: unknown): { status: number; message: string } | null {
  if (typeof error !== "object" || error === null) return null;
  const status = (error as { status?: unknown }).status;
  const code = (error as { code?: unknown }).code;
  if (typeof status !== "number" || typeof code !== "string") return null;
  const message = (error as { message?: unknown }).message;
  return { status, message: typeof message === "string" ? message : "" };
}
