import type { ChatTurnSnapshot } from "@/api/models";

type Translate = ReturnType<typeof import("@/i18n").useI18n>["t"];

export function turnFailureMessage(turn: ChatTurnSnapshot, t: Translate): string {
  const hasImage = turn.attachments.some((item) => /\.(?:jpe?g|png|webp)$/i.test(item.caption ?? ""));
  if (turn.failure_code === "vision_unavailable") return t("turn.failure.visionUnavailable");
  if (turn.failure_code === "unsupported_input") {
    return hasImage ? t("turn.failure.imageUnsupported") : t("turn.failure.unsupportedInput");
  }
  if (turn.failure_code === "image_input_rejected") return t("turn.failure.imageRejected");
  if (turn.failure_code === "provider_failed" || turn.failure_code === "remote_provider_failed") {
    return t("turn.failure.providerFailed");
  }
  if (turn.failure_code === "service_restarted") return t("turn.failure.serviceRestarted");
  if (turn.failure_code === "runtime_failed") return t("turn.failure.runtimeFailed");
  if (turn.failure_code) return t("turn.failure.other", { code: turn.failure_code });
  return t("turn.failure.runtimeFailed");
}
