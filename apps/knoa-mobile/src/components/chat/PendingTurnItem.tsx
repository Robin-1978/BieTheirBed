import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import type { PendingChatTurn } from "./types";
import { attachmentStatusLabel } from "./types";
import { AppPressable } from "@/components/AppPressable";
import { formatMessageTimestamp } from "@/ui/formatRelativeTime";
import { useI18n } from "@/i18n";
import { colors, radii, shadows, spacing } from "@/theme";

export type PendingTurnItemProps = {
  pending: PendingChatTurn;
  queued: boolean;
  showTimestamp: boolean;
  timestampMs: number;
  locale: string;
  onCopy(text: string): void;
  onRetry(pending: PendingChatTurn): void;
  onEdit(pending: PendingChatTurn): void;
};

export function PendingTurnItem({
  pending,
  queued,
  showTimestamp,
  timestampMs,
  locale,
  onCopy,
  onRetry,
  onEdit,
}: PendingTurnItemProps) {
  const { t } = useI18n();
  const timestampLabel = formatMessageTimestamp(timestampMs, locale, t("chat.messageTimeYesterday"));

  return (
    <View style={styles.turn}>
      {showTimestamp ? <Text style={styles.messageTimestamp}>{timestampLabel}</Text> : null}
      <Pressable
        accessibilityRole="button"
        delayLongPress={320}
        onLongPress={() => onCopy(pending.userInput)}
        style={styles.userBubble}
      >
        <Text style={styles.userText}>{pending.userInput}</Text>
        {pending.attachments.length ? (
          <Text style={styles.userMeta}>
            {t("chat.attachments", { count: pending.attachments.length })}
          </Text>
        ) : null}
        {pending.attachments.length ? (
          <View style={styles.pendingAttachments}>
            {pending.attachments.map((item, index) => (
              <View key={`${item.uri}:${index}`} style={styles.pendingAttachmentRow}>
                {item.status === "uploading" ? (
                  <ActivityIndicator color={colors.accentSoft} size="small" />
                ) : null}
                <Text numberOfLines={1} style={styles.pendingAttachmentName}>{item.name}</Text>
                <Text style={[styles.pendingAttachmentState, item.status === "failed" && styles.pendingAttachmentFailed]}>
                  {attachmentStatusLabel(item.status ?? "pending", t)}
                </Text>
              </View>
            ))}
          </View>
        ) : null}
      </Pressable>

      <View style={styles.assistantBubble}>
        <View style={styles.activityRow}>
          {pending.state === "sending" ? <ActivityIndicator color={colors.accent} size="small" /> : null}
          <Text style={pending.state === "failed" ? styles.pendingError : styles.activity}>
            {pending.state === "sending"
              ? (queued ? t("chat.queued") : t("chat.sending"))
              : pending.error || t("chat.sendFailed")}
          </Text>
        </View>
        {pending.state === "failed" ? (
          <View style={styles.turnActions}>
            <AppPressable accessibilityRole="button" onPress={() => onRetry(pending)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("chat.retry")}</Text>
            </AppPressable>
            <AppPressable accessibilityRole="button" onPress={() => onEdit(pending)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("taskDetail.edit")}</Text>
            </AppPressable>
          </View>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  turn: { gap: spacing.small },
  messageTimestamp: { alignSelf: "center", color: colors.muted, fontSize: 11, marginBottom: 2 },
  userBubble: {
    alignSelf: "flex-end",
    maxWidth: "84%",
    backgroundColor: colors.accent,
    borderRadius: radii.large,
    borderBottomRightRadius: 5,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.medium,
  },
  userText: { color: colors.onAccent, fontSize: 16, lineHeight: 23 },
  userMeta: { color: colors.accentSoft, fontSize: 12, marginTop: spacing.xsmall },
  pendingAttachments: { marginTop: spacing.small, gap: spacing.xsmall },
  pendingAttachmentRow: { minHeight: 24, flexDirection: "row", alignItems: "center", gap: spacing.small },
  pendingAttachmentName: { color: colors.onAccent, flex: 1, fontSize: 12 },
  pendingAttachmentState: { color: colors.accentSoft, fontSize: 10 },
  pendingAttachmentFailed: { color: "#FFD1CC" },
  assistantBubble: {
    alignSelf: "stretch",
    width: "100%",
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderBottomLeftRadius: 5,
    padding: spacing.large,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  activityRow: { flexDirection: "row", alignItems: "center", gap: spacing.small },
  activity: { color: colors.muted },
  pendingError: { color: colors.danger, flex: 1 },
  turnActions: { flexDirection: "row", gap: spacing.medium, marginTop: spacing.medium },
  turnAction: {
    paddingHorizontal: spacing.medium,
    paddingVertical: 8,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  turnActionText: { color: colors.ink, fontWeight: "700", fontSize: 12 },
});
