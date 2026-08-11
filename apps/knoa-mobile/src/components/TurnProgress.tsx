import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import type { ChatTurnSnapshot } from "@/api/models";
import { colors } from "@/theme";
import { timelineDisplayEntries, type TimelineDisplayEntry } from "./turnTimeline";

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export function TurnProgress({ turn }: { turn: ChatTurnSnapshot }) {
  const active = !TERMINAL_STATES.has(turn.state);
  const entries = useMemo(() => timelineDisplayEntries(turn.timeline), [turn.timeline]);
  const [expanded, setExpanded] = useState(active);

  useEffect(() => {
    setExpanded(active);
  }, [active]);

  if (!active && !entries.length) return null;

  return (
    <View style={styles.root}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={expanded ? "收起执行过程" : "查看执行过程"}
        disabled={active && !entries.length}
        onPress={() => setExpanded((current) => !current)}
        style={styles.header}
      >
        {active ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.done}>✓</Text>}
        <Text style={styles.label}>{progressLabel(turn, entries)}</Text>
        {entries.length ? <Text style={styles.toggle}>{expanded ? "收起" : "查看"}</Text> : null}
      </Pressable>
      {expanded && entries.length ? (
        <View style={styles.details}>
          {entries.map((entry) => (
            <TimelineRow entry={entry} key={entry.key} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

function TimelineRow({ entry }: { entry: TimelineDisplayEntry }) {
  if (entry.kind === "reasoning") {
    return <Text style={styles.thought}>› {compact(entry.content, 520)}</Text>;
  }
  if (entry.kind === "content") {
    return (
      <View style={styles.draft}>
        <Text style={styles.stepTitle}>组织回答</Text>
        <Text style={styles.stepText}>{compact(entry.content, 520)}</Text>
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
          {entry.state === "failed" ? "未完成" : entry.state === "completed" ? "完成" : "进行中"}
        </Text>
      </View>
    );
  }
  return <Text style={styles.notice}>{compact(entry.content, 360)}</Text>;
}

function progressLabel(turn: ChatTurnSnapshot, entries: TimelineDisplayEntry[]): string {
  if (turn.state === "waiting_approval") return "等待你的确认";
  if (turn.state === "failed") return `执行未完成 · ${entries.length} 步`;
  if (turn.state === "cancelled") return `已停止 · ${entries.length} 步`;
  if (turn.state === "completed") return `执行过程 · ${entries.length} 步`;
  const latest = entries.at(-1);
  if (!latest) return "正在开始执行";
  if (latest.kind === "reasoning") return "正在分析";
  if (latest.kind === "content") return "正在组织回答";
  if (latest.kind === "tool") {
    return latest.state === "running"
      ? `正在调用 ${latest.toolName}`
      : `${latest.toolName}${latest.state === "failed" ? "未完成" : "已完成"}，继续处理`;
  }
  return latest.content.trim() || "正在继续执行";
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
  notice: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
