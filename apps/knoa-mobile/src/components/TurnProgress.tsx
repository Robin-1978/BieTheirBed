import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import type { ChatTimelineEntry, ChatTurnSnapshot } from "@/api/models";
import { colors } from "@/theme";

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

type ToolProgress = {
  key: string;
  name: string;
  state: "running" | "completed" | "failed";
};

export function TurnProgress({ turn }: { turn: ChatTurnSnapshot }) {
  const active = !TERMINAL_STATES.has(turn.state);
  const tools = useMemo(() => collectToolProgress(turn.timeline), [turn.timeline]);
  const notices = useMemo(
    () => turn.timeline.filter((entry) => entry.kind === "notice" && entry.content.trim()).slice(-2),
    [turn.timeline],
  );
  const reasoning = compactTail(turn.reasoning, 420);
  const hasDetails = Boolean(reasoning || tools.length || notices.length);
  const [expanded, setExpanded] = useState(active);

  useEffect(() => {
    setExpanded(active);
  }, [active]);

  if (!active && !hasDetails) return null;

  const label = progressLabel(turn, tools);
  return (
    <View style={styles.root}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={expanded ? "收起执行过程" : "查看执行过程"}
        disabled={active && !hasDetails}
        onPress={() => setExpanded((current) => !current)}
        style={styles.header}
      >
        {active ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.done}>✓</Text>}
        <Text style={styles.label}>{label}</Text>
        {hasDetails ? <Text style={styles.toggle}>{expanded ? "收起" : "查看"}</Text> : null}
      </Pressable>
      {expanded && hasDetails ? (
        <View style={styles.details}>
          {reasoning ? (
            <View style={styles.reasoning}>
              <Text style={styles.detailTitle}>思考</Text>
              <Text style={styles.detailText}>{reasoning}</Text>
            </View>
          ) : null}
          {tools.slice(-4).map((tool) => (
            <View key={tool.key} style={styles.toolRow}>
              {tool.state === "running"
                ? <ActivityIndicator color={colors.accent} size="small" />
                : <Text style={tool.state === "failed" ? styles.failed : styles.done}>
                    {tool.state === "failed" ? "!" : "✓"}
                  </Text>}
              <Text numberOfLines={1} style={styles.toolName}>{tool.name || "工具"}</Text>
              <Text style={styles.toolState}>{toolStateLabel(tool.state)}</Text>
            </View>
          ))}
          {notices.map((entry, index) => (
            <Text key={`${entry.iteration}:${index}`} style={styles.notice}>{entry.content}</Text>
          ))}
        </View>
      ) : null}
    </View>
  );
}

function collectToolProgress(timeline: ChatTimelineEntry[]): ToolProgress[] {
  const ordered: ToolProgress[] = [];
  const positions = new Map<string, number>();
  for (const [index, entry] of timeline.entries()) {
    if (entry.kind !== "tool_call" && entry.kind !== "tool_result") continue;
    const key = entry.tool_call_id || `${entry.tool_name}:${entry.iteration}:${index}`;
    const existing = positions.get(key);
    if (entry.kind === "tool_call") {
      if (existing === undefined) {
        positions.set(key, ordered.length);
        ordered.push({
          key,
          name: entry.tool_name,
          state: entry.blocked ? "failed" : "running",
        });
      }
      continue;
    }
    const state = entry.blocked ? "failed" : "completed";
    if (existing === undefined) {
      positions.set(key, ordered.length);
      ordered.push({ key, name: entry.tool_name, state });
    } else {
      const current = ordered[existing];
      if (current) {
        ordered[existing] = { ...current, name: entry.tool_name || current.name, state };
      }
    }
  }
  return ordered;
}

function progressLabel(turn: ChatTurnSnapshot, tools: ToolProgress[]): string {
  if (turn.state === "waiting_approval") return "等待你的确认";
  if (turn.state === "failed") return `执行未完成${tools.length ? ` · ${tools.length} 步` : ""}`;
  if (turn.state === "cancelled") return `已停止${tools.length ? ` · ${tools.length} 步` : ""}`;
  if (turn.state === "completed") return `执行过程${tools.length ? ` · ${tools.length} 步` : ""}`;
  const running = [...tools].reverse().find((tool) => tool.state === "running");
  if (running) return `正在使用 ${running.name || "工具"}`;
  if (turn.content) return "正在组织回答";
  return "正在分析";
}

function compactTail(value: string, maxLength: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= maxLength) return compact;
  return `…${compact.slice(-maxLength)}`;
}

function toolStateLabel(state: ToolProgress["state"]): string {
  if (state === "running") return "进行中";
  if (state === "failed") return "未完成";
  return "完成";
}

const styles = StyleSheet.create({
  root: { marginTop: 10, borderRadius: 12, backgroundColor: colors.background, overflow: "hidden" },
  header: { minHeight: 42, paddingHorizontal: 11, flexDirection: "row", alignItems: "center", gap: 9 },
  label: { color: colors.muted, flex: 1, fontSize: 13, fontWeight: "600" },
  toggle: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  details: { borderTopWidth: 1, borderTopColor: colors.line, padding: 11, gap: 9 },
  reasoning: { gap: 4 },
  detailTitle: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  detailText: { color: colors.ink, fontSize: 13, lineHeight: 19 },
  toolRow: { minHeight: 28, flexDirection: "row", alignItems: "center", gap: 8 },
  toolName: { color: colors.ink, flex: 1, fontSize: 13 },
  toolState: { color: colors.muted, fontSize: 12 },
  done: { color: colors.accent, fontWeight: "800", width: 18, textAlign: "center" },
  failed: { color: colors.danger, fontWeight: "800", width: 18, textAlign: "center" },
  notice: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
