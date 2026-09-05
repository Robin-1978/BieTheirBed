import { memo, useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type {
  ChatApproval,
  ChatTurnSnapshot,
  HumanInteraction,
} from "@/api/models";
import type { AssistantArtifactItem as AssistantArtifactItemType, ResolvedArtifactFile } from "@/api/chatArtifacts";
import { assistantArtifactItems } from "@/api/chatArtifacts";
import { TERMINAL_STATES } from "./types";
import { AssistantArtifactItem } from "./AssistantArtifactItem";
import { ChatApprovalCard } from "./ChatApprovalCard";
import { AppIcon } from "@/components/AppIcon";
import { AppMarkdown } from "@/components/AppMarkdown";
import { InteractionCard } from "@/components/InteractionCard";
import { TurnProgress } from "@/components/TurnProgress";
import { formatMessageTimestamp } from "@/ui/formatRelativeTime";
import { useI18n } from "@/i18n";
import { colors, radii, shadows, spacing } from "@/theme";

export type ChatTurnItemProps = {
  turn: ChatTurnSnapshot;
  showTimestamp: boolean;
  timestampMs: number;
  locale: string;
  onCopy(text: string): void;
  resolving: string;
  resolvingApproved: boolean | null;
  resolvingInteraction: string;
  onResolve(approval: ChatApproval, approved: boolean): void;
  onResolveInteraction(interaction: HumanInteraction, value: Record<string, unknown>): void;
  onLoadArtifact(item: AssistantArtifactItemType): Promise<ResolvedArtifactFile>;
  onOpenArtifact(item: AssistantArtifactItemType): Promise<void>;
  onSaveArtifact(item: AssistantArtifactItemType): Promise<void>;
  onRetry(turn: ChatTurnSnapshot): void;
  onEdit(turn: ChatTurnSnapshot): void;
  onConvertToTask?(turn: ChatTurnSnapshot): void;
};

export const ChatTurnItem = memo(function ChatTurnItem({
  turn,
  showTimestamp,
  timestampMs,
  locale,
  onCopy,
  resolving,
  resolvingApproved,
  resolvingInteraction,
  onResolve,
  onResolveInteraction,
  onLoadArtifact,
  onOpenArtifact,
  onSaveArtifact,
  onRetry,
  onEdit,
  onConvertToTask,
}: ChatTurnItemProps) {
  const { t } = useI18n();
  const terminal = TERMINAL_STATES.has(turn.state);
  const response = terminal ? turn.final_output || turn.content : "";
  const approval = turn.approvals.find((item) => item.state === "pending") ?? null;
  const interaction = turn.interactions?.find((item) => item.state === "pending") ?? null;
  const artifactItems = useMemo(() => assistantArtifactItems(turn.artifacts), [turn.artifacts]);
  const timestampLabel = formatMessageTimestamp(timestampMs, locale, t("chat.messageTimeYesterday"));

  return (
    <View style={styles.turn}>
      {showTimestamp ? <Text style={styles.messageTimestamp}>{timestampLabel}</Text> : null}

      <Pressable
        accessibilityRole="button"
        delayLongPress={320}
        onLongPress={() => onCopy(turn.user_input)}
        style={styles.userBubble}
      >
        <Text style={styles.userText}>{turn.user_input}</Text>
        {turn.attachments.length ? (
          <Text style={styles.userMeta}>
            {t("chat.attachments", { count: turn.attachments.length })}
          </Text>
        ) : null}
      </Pressable>

      <View style={styles.assistantBubble}>
        <TurnProgress turn={turn} />

        {response ? (
          <Pressable accessibilityRole="button" delayLongPress={320} onLongPress={() => onCopy(response)}>
            <AppMarkdown value={response} style={styles.markdownList} />
          </Pressable>
        ) : null}

        {artifactItems.length ? (
          <View style={styles.generatedArtifacts}>
            {artifactItems.map((item) => (
              <AssistantArtifactItem
                key={item.key}
                item={item}
                onLoad={onLoadArtifact}
                onOpen={onOpenArtifact}
                onSave={onSaveArtifact}
              />
            ))}
          </View>
        ) : null}

        {interaction ? (
          <InteractionCard
            interaction={interaction}
            submitting={resolvingInteraction === interaction.interaction_id}
            onSubmit={(value) => onResolveInteraction(interaction, value)}
          />
        ) : null}

        {approval ? (
          <ChatApprovalCard
            approval={approval}
            resolving={resolving}
            resolvingApproved={resolvingApproved}
            onResolve={onResolve}
          />
        ) : null}

        {turn.state === "completed" && response ? (
          <View style={styles.completedActions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("chat.copyResponse")}
              onPress={() => onCopy(response)}
              style={styles.completedAction}
            >
              <AppIcon name="file" color={colors.muted} size={12} />
              <Text style={styles.completedActionText}>{t("chat.copyShort")}</Text>
            </Pressable>
            {onConvertToTask ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={t("chat.convertToTask")}
                onPress={() => onConvertToTask(turn)}
                style={[styles.completedAction, styles.convertAction]}
              >
                <AppIcon name="timer" color={colors.accent} size={12} />
                <Text style={[styles.completedActionText, styles.convertActionText]}>
                  {t("chat.convertToTask")}
                </Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {turn.state === "failed" || turn.state === "cancelled" ? (
          <View style={styles.turnActions}>
            <Pressable accessibilityRole="button" onPress={() => onRetry(turn)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("chat.retry")}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => onEdit(turn)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("chat.editResend")}</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  turn: { gap: spacing.small },
  messageTimestamp: { alignSelf: "center", color: colors.muted, fontSize: 11, marginBottom: 2 },
  completedActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    marginTop: spacing.small,
    paddingTop: spacing.xsmall,
    borderTopWidth: 1,
    borderTopColor: colors.line,
  },
  completedAction: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radii.small,
    backgroundColor: colors.surfaceMuted,
  },
  completedActionText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600",
  },
  convertAction: {
    backgroundColor: colors.accentSoft,
  },
  convertActionText: {
    color: colors.accent,
  },
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
  markdownList: { width: "100%", alignSelf: "stretch" },
  generatedArtifacts: {
    gap: spacing.small,
    marginTop: spacing.medium,
  },
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
