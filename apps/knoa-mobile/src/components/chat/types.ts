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
