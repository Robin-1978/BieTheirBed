import { useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import type { ChatTurnSnapshot } from "@/api/models";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";
import { timelineDisplayEntries, type TimelineDisplayEntry } from "./turnTimeline";
import { turnFailureMessage } from "./turnFailurePresentation";

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export function TurnProgress({ turn }: { turn: ChatTurnSnapshot }) {
  const { t } = useI18n();
  const userStatus = turn.work_status?.status;
  const active = userStatus ? !turn.work_status?.terminal : !TERMINAL_STATES.has(turn.state);
  const failed = userStatus ? userStatus === "failed" : turn.state === "failed";
  const entries = useMemo(
    () => timelineDisplayEntries(turn.timeline, turn.final_output),
    [turn.final_output, turn.timeline],
  );
  const [expanded, setExpanded] = useState(false);

  if (!active && !entries.length && !failed) return null;

  return (
    <View style={styles.root}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={expanded ? t("turn.collapse") : t("turn.expand")}
        disabled={active && !entries.length}
        onPress={() => setExpanded((current) => !current)}
        style={styles.header}
      >
        {active
          ? <ActivityIndicator color={colors.accent} size="small" />
          : <Text style={failed ? styles.failed : styles.done}>{failed ? "!" : "✓"}</Text>}
        <Text style={styles.label}>{progressLabel(turn, entries, t)}</Text>
        {entries.length ? <Text style={styles.toggle}>{expanded ? t("turn.collapseShort") : t("turn.view")}</Text> : null}
      </Pressable>
      {failed ? (
        <View>
          <Text accessibilityRole="alert" style={styles.failureDetail}>{turnFailureMessage(turn, t)}</Text>
          {turn.work_status?.side_effect === "unknown" ? <Text style={styles.failureImpact}>{t("turn.failure.sideEffectUnknown")}</Text> : null}
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

function TimelineRow({ entry, t }: { entry: TimelineDisplayEntry; t: ReturnType<typeof useI18n>["t"] }) {
  if (entry.kind === "reasoning") {
    return <Text style={styles.thought}>› {compact(entry.content, 520)}</Text>;
  }
  if (entry.kind === "content") {
    return (
      <View style={styles.draft}>
        <Text style={styles.stepTitle}>{t("turn.compose")}</Text>
        <Text style={styles.stepText}>{compact(entry.content, 520)}</Text>
      </View>
    );
  }
  if (entry.kind === "completion") {
    return (
      <View style={styles.toolRow}>
        <Text style={styles.done}>✓</Text>
        <Text style={styles.toolName}>{t("turn.answerCompleted")}</Text>
      </View>
    );
  }
  if (entry.kind === "tool") {
    return (
      <View style={styles.toolRow}>
        <Text style={entry.state === "failed" ? styles.failed : entry.state === "completed" ? styles.done : styles.runningDot}>
          {entry.state === "failed" ? "!" : entry.state === "completed" ? "✓" : "•"}
        </Text>
        <Text style={styles.toolName}>{entry.toolName}</Text>
        <Text style={styles.toolState}>
          {entry.state === "failed" ? t("turn.failed") : entry.state === "completed" ? t("turn.completed") : t("turn.running")}
        </Text>
      </View>
    );
  }
  return <Text style={styles.notice}>{compact(entry.content, 360)}</Text>;
}

function progressLabel(turn: ChatTurnSnapshot, entries: TimelineDisplayEntry[], t: ReturnType<typeof useI18n>["t"]): string {
  const status = turn.work_status?.status;
  if (status === "waiting_for_you" || turn.state === "waiting_approval") return t("turn.waitingApproval");
  if (status === "failed" || turn.state === "failed") return t("turn.executionFailed", { count: entries.length });
  if (status === "cancelled" || turn.state === "cancelled") return t("turn.stopped", { count: entries.length });
  if (status === "completed" || turn.state === "completed") return t("turn.process", { count: entries.length });
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
  root: { marginTop: 10, borderRadius: 12, backgroundColor: colors.background, overflow: "hidden" },
  header: { minHeight: 42, paddingHorizontal: 11, flexDirection: "row", alignItems: "center", gap: 9 },
  label: { color: colors.muted, flex: 1, fontSize: 13, fontWeight: "600" },
  toggle: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  details: { borderTopWidth: 1, borderTopColor: colors.line, padding: 11, gap: 10 },
  thought: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  draft: { gap: 3 },
  stepTitle: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  stepText: { color: colors.ink, fontSize: 13, lineHeight: 19 },
  toolRow: { minHeight: 25, flexDirection: "row", alignItems: "center", gap: 8 },
  toolName: { color: colors.ink, flex: 1, fontSize: 13 },
  toolState: { color: colors.muted, fontSize: 12 },
  runningDot: { color: colors.accent, fontSize: 20, fontWeight: "900", width: 18, textAlign: "center" },
  done: { color: colors.accent, fontWeight: "800", width: 18, textAlign: "center" },
  failed: { color: colors.danger, fontWeight: "800", width: 18, textAlign: "center" },
  failureDetail: { color: colors.danger, fontSize: 13, lineHeight: 19, paddingHorizontal: 11, paddingBottom: 11 },
  failureImpact: { color: colors.warning, fontSize: 13, lineHeight: 19, paddingHorizontal: 11, paddingBottom: 11 },
  notice: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
