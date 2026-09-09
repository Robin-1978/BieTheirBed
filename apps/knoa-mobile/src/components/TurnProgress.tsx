import { useMemo, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import type { ChatTurnSnapshot } from "@/api/models";
import { useI18n } from "@/i18n";
import { colors, radii } from "@/theme";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { timelineDisplayEntries, type TimelineDisplayEntry } from "./turnTimeline";
import { turnFailureMessage } from "./turnFailurePresentation";

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export function TurnProgress({ turn }: { turn: ChatTurnSnapshot }) {
  const { t } = useI18n();
  const userStatus = turn.work_status?.status;
  const active = userStatus ? !turn.work_status?.terminal : !TERMINAL_STATES.has(turn.state);
  const failed = userStatus ? userStatus === "failed" : turn.state === "failed";
  const entries = useMemo(
    () => timelineDisplayEntries(turn.timeline, turn.final_output, turn.reasoning),
    [turn.final_output, turn.reasoning, turn.timeline],
  );

  // Auto-expand during active thought & execution; collapse on finish with manual toggle capability
  const [userToggled, setUserToggled] = useState<boolean | null>(null);
  const expanded = userToggled !== null ? userToggled : active;

  if (!active && !entries.length && !failed) return null;

  return (
    <View style={styles.root}>
      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={expanded ? t("turn.collapse") : t("turn.expand")}
        disabled={active && !entries.length}
        onPress={() => setUserToggled(!expanded)}
        style={styles.header}
      >
        {active ? (
          <ActivityIndicator color={colors.accent} size="small" />
        ) : (
          <View style={[styles.statusIconWrap, failed ? styles.statusFailedWrap : styles.statusDoneWrap]}>
            <Text style={failed ? styles.failedText : styles.doneText}>{failed ? "!" : "✓"}</Text>
          </View>
        )}
        <Text numberOfLines={1} style={styles.label}>
          {progressLabel(turn, entries, t)}
        </Text>
        {entries.length ? (
          <View style={styles.toggleWrap}>
            <Text style={styles.toggleText}>
              {expanded ? t("turn.collapseShort") : t("turn.view")}
            </Text>
            <AppIcon
              name={expanded ? "chevron-up" : "chevron-down"}
              size={14}
              color={colors.accent}
            />
          </View>
        ) : null}
      </AppPressable>

      {failed ? (
        <View>
          <Text accessibilityRole="alert" style={styles.failureDetail}>
            {turnFailureMessage(turn, t)}
          </Text>
          {turn.work_status?.side_effect === "unknown" ? (
            <Text style={styles.failureImpact}>{t("turn.failure.sideEffectUnknown")}</Text>
          ) : null}
        </View>
      ) : null}

      {expanded && entries.length ? (
        <View style={styles.details}>
          {entries.map((entry) => (
            <TimelineRow entry={entry} key={entry.key} t={t} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

function getToolIconName(toolName: string): AppIconName {
  const name = toolName.toLowerCase();
  if (name.includes("sleep") || name.includes("wait")) return "timer";
  if (name.includes("search") || name.includes("fetch")) return "globe";
  if (name.includes("file") || name.includes("artifact")) return "file";
  if (name.includes("command") || name.includes("exec") || name.includes("bash")) return "code";
  return "agent";
}

function TimelineRow({ entry, t }: { entry: TimelineDisplayEntry; t: ReturnType<typeof useI18n>["t"] }) {
  if (entry.kind === "reasoning") {
    return (
      <View style={styles.thoughtBox}>
        <View style={styles.thoughtHeader}>
          <AppIcon name="agent" size={13} color={colors.accent} />
          <Text style={styles.thoughtTitle}>{t("turn.chainTitle") || "思考过程"}</Text>
        </View>
        <Text style={styles.thoughtText}>{entry.content.trim()}</Text>
      </View>
    );
  }

  if (entry.kind === "tool") {
    const iconName = getToolIconName(entry.toolName);
    return (
      <View style={styles.toolRow}>
        <View style={styles.toolIconBadge}>
          <AppIcon name={iconName} size={13} color={colors.muted} />
        </View>
        <View style={styles.toolInfo}>
          <Text style={styles.toolName}>{entry.toolName}</Text>
          {entry.detail ? (
            <Text numberOfLines={1} style={styles.toolDetail}>
              {entry.detail}
            </Text>
          ) : null}
        </View>
        <View style={styles.toolStatusBadge}>
          {entry.state === "running" ? (
            <ActivityIndicator color={colors.accent} size="small" style={styles.toolSpinner} />
          ) : (
            <Text style={entry.state === "failed" ? styles.toolFailedText : styles.toolDoneText}>
              {entry.state === "failed" ? "✕" : "✓"}
            </Text>
          )}
          <Text style={styles.toolState}>
            {entry.state === "failed"
              ? t("turn.failed")
              : entry.state === "completed"
              ? t("turn.completed")
              : t("turn.running")}
          </Text>
        </View>
      </View>
    );
  }

  if (entry.kind === "content") {
    return (
      <View style={styles.draft}>
        <Text style={styles.stepTitle}>{t("turn.compose")}</Text>
        <Text style={styles.stepText}>{entry.content.trim()}</Text>
      </View>
    );
  }

  if (entry.kind === "completion") {
    return (
      <View style={styles.completionRow}>
        <Text style={styles.doneText}>✓</Text>
        <Text style={styles.completionText}>{t("turn.answerCompleted")}</Text>
      </View>
    );
  }

  return <Text style={styles.notice}>{compact(entry.content, 360)}</Text>;
}

function progressLabel(
  turn: ChatTurnSnapshot,
  entries: TimelineDisplayEntry[],
  t: ReturnType<typeof useI18n>["t"],
): string {
  const status = turn.work_status?.status;
  if (status === "waiting_for_you" || turn.state === "waiting_approval") return t("turn.waitingApproval");
  if (status === "failed" || turn.state === "failed") return t("turn.executionFailed", { count: entries.length });
  if (status === "cancelled" || turn.state === "cancelled") return t("turn.stopped", { count: entries.length });

  if (status === "completed" || turn.state === "completed") {
    const toolCount = entries.filter((e) => e.kind === "tool").length;
    const thoughtCount = entries.filter((e) => e.kind === "reasoning").length;
    if (toolCount > 0 && thoughtCount > 0) {
      return (
        t("turn.thoughtAndTools", { thoughts: thoughtCount, tools: toolCount }) ||
        `思考与行动链 · ${thoughtCount} 思考 / ${toolCount} 工具`
      );
    }
    if (toolCount > 0) {
      return t("turn.toolCompletedCount", { count: toolCount }) || `已调用 ${toolCount} 个工具`;
    }
    if (thoughtCount > 0) {
      return t("turn.thoughtCompleted") || `已完成深度思考`;
    }
    return t("turn.process", { count: entries.length });
  }

  const latest = entries.at(-1);
  if (!latest) return t("turn.starting");
  if (latest.kind === "reasoning") return t("turn.analyzing");
  if (latest.kind === "content") return t("turn.composing");
  if (latest.kind === "completion") return t("turn.answerCompleted");
  if (latest.kind === "tool") {
    return latest.state === "running"
      ? t("turn.callingTool", { tool: latest.toolName })
      : t(latest.state === "failed" ? "turn.toolFailed" : "turn.toolCompleted", { tool: latest.toolName });
  }
  return latest.content.trim() || t("turn.continuing");
}

function compact(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength)}…`;
}

const styles = StyleSheet.create({
  root: {
    marginTop: 8,
    borderRadius: radii.medium || 12,
    backgroundColor: colors.background,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: colors.line,
  },
  header: {
    minHeight: 40,
    paddingHorizontal: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 9,
  },
  statusIconWrap: {
    width: 18,
    height: 18,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  statusDoneWrap: {
    backgroundColor: colors.surfaceMuted,
  },
  statusFailedWrap: {
    backgroundColor: colors.surfaceMuted,
  },
  doneText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  failedText: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "700",
  },
  label: {
    flex: 1,
    fontSize: 13,
    color: colors.ink,
    fontWeight: "500",
  },
  toggleWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
  },
  toggleText: {
    fontSize: 12,
    color: colors.accent,
    fontWeight: "600",
  },
  details: {
    paddingHorizontal: 10,
    paddingBottom: 10,
    gap: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
    paddingTop: 8,
  },
  thoughtBox: {
    padding: 10,
    borderRadius: radii.small || 8,
    backgroundColor: colors.surfaceMuted,
    borderLeftWidth: 3,
    borderLeftColor: colors.accent,
    gap: 6,
  },
  thoughtHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
  },
  thoughtTitle: {
    fontSize: 11,
    fontWeight: "600",
    color: colors.accent,
  },
  thoughtText: {
    fontSize: 12,
    color: colors.ink,
    lineHeight: 18,
    opacity: 0.9,
  },
  toolRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 7,
    paddingHorizontal: 9,
    borderRadius: radii.small || 8,
    backgroundColor: colors.surfaceMuted,
  },
  toolIconBadge: {
    width: 22,
    height: 22,
    borderRadius: 6,
    backgroundColor: colors.background,
    alignItems: "center",
    justifyContent: "center",
  },
  toolInfo: {
    flex: 1,
    gap: 2,
  },
  toolName: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.ink,
    fontFamily: "monospace",
  },
  toolDetail: {
    fontSize: 11,
    color: colors.muted,
  },
  toolStatusBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  toolSpinner: {
    transform: [{ scale: 0.75 }],
  },
  toolDoneText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
  },
  toolFailedText: {
    color: colors.danger,
    fontSize: 11,
    fontWeight: "700",
  },
  toolState: {
    fontSize: 11,
    color: colors.muted,
  },
  draft: {
    padding: 9,
    borderRadius: radii.small || 8,
    backgroundColor: colors.surfaceMuted,
    gap: 4,
  },
  stepTitle: {
    fontSize: 11,
    color: colors.muted,
    fontWeight: "600",
  },
  stepText: {
    fontSize: 12,
    color: colors.ink,
    lineHeight: 18,
  },
  completionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 4,
    paddingHorizontal: 4,
  },
  completionText: {
    fontSize: 12,
    color: colors.muted,
  },
  notice: {
    fontSize: 12,
    color: colors.muted,
  },
  failureDetail: {
    fontSize: 12,
    color: colors.danger,
    paddingHorizontal: 11,
    paddingBottom: 8,
  },
  failureImpact: {
    fontSize: 11,
    color: colors.muted,
    paddingHorizontal: 11,
    paddingBottom: 8,
  },
});
