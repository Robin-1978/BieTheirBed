import { useLocalSearchParams } from "expo-router";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { ApprovalRequest, TaskEvent, TaskSnapshot, TaskTraceEntry } from "@/api/models";
import { AppMarkdown } from "@/components/AppMarkdown";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function TaskDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const taskId = String(params.id ?? "");
  const gateway = useGateway();
  const [task, setTask] = useState<TaskSnapshot | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [working, setWorking] = useState(false);

  const refresh = useCallback(async () => {
    if (!gateway.client || !taskId) return;
    const [snapshot, timeline] = await gateway.runAuthenticated((client) => Promise.all([
      client.getTask(taskId),
      client.taskEvents(taskId),
    ]));
    setTask(snapshot);
    setEvents(timeline);
  }, [gateway.client, gateway.runAuthenticated, taskId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const event = gateway.latestEvent?.event;
    if (!event || event.task_id !== taskId) return;
    setEvents((current) => current.some((item) => item.event_seq === event.event_seq)
      ? current
      : [...current, event]);
    if (["state_changed", "approval_requested", "approval_resolved", "completed", "failed", "cancelled"].includes(event.event_type)) {
      void gateway.runAuthenticated((client) => client.getTask(taskId)).then(setTask);
    }
  }, [gateway.latestEvent, gateway.runAuthenticated, taskId]);

  const approval = useMemo(() => pendingApproval(events), [events]);
  const timeline = useMemo(
    () => (task?.trace?.entries ?? []).filter(
      (entry) => entry.entry_type !== "final_output"
        && (entry.entry_type !== "content" || !task?.final_summary),
    ),
    [task?.final_summary, task?.trace?.entries],
  );

  async function command(action: "cancel" | "pause" | "resume" | "retry") {
    if (!gateway.client || !task) return;
    setWorking(true);
    try {
      await gateway.runAuthenticated((client) => client.taskCommand(task.task_id, action));
      await refresh();
    } finally {
      setWorking(false);
    }
  }

  async function resolve(approved: boolean) {
    if (!gateway.client || !approval) return;
    setWorking(true);
    try {
      await gateway.runAuthenticated(
        (client) => client.resolveApproval(approval.approvalId, approved),
      );
      await refresh();
    } finally {
      setWorking(false);
    }
  }

  async function openArtifact(artifactId: string) {
    if (!gateway.client) return;
    const downloaded = await gateway.runAuthenticated((client) => client.downloadArtifact(
      gateway.sessionHandle,
      artifactId,
    ));
    const safeName = downloaded.name.replace(/[^\p{L}\p{N}._ -]/gu, "_");
    const file = new File(Paths.cache, `${artifactId}-${safeName}`);
    file.create({ overwrite: true, intermediates: true });
    file.write(downloaded.bytes);
    await Sharing.shareAsync(file.uri, { mimeType: downloaded.mediaType });
  }

  if (!task) {
    return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.summary}>
        <View style={styles.summaryHeader}>
          <Text style={styles.origin}>{originLabel(task.origin)}</Text>
          <Text style={styles.state}>{stateLabel(task.state)}</Text>
        </View>
        <Text style={styles.goal}>{task.goal}</Text>
        {task.phase ? <Text style={styles.phase}>{task.phase}</Text> : null}
      </View>

      {approval ? (
        <View style={styles.approval}>
          <Text style={styles.approvalTitle}>需要你的确认</Text>
          <Text style={styles.tool}>{approval.toolName}</Text>
          <Text style={styles.reason}>{approval.reason}</Text>
          <View style={styles.row}>
            <Pressable style={styles.deny} onPress={() => void resolve(false)} disabled={working}>
              <Text style={styles.denyText}>取消</Text>
            </Pressable>
            <Pressable style={styles.approve} onPress={() => void resolve(true)} disabled={working}>
              <Text style={styles.approveText}>确认</Text>
            </Pressable>
          </View>
        </View>
      ) : null}

      {task.final_summary ? (
        <View style={styles.final}>
          <AppMarkdown value={task.final_summary} style={styles.markdown} />
        </View>
      ) : null}

      <View style={styles.timeline}>
        {timeline.map((entry, index) => (
          <TraceEntry
            key={`${entry.entry_type}:${entry.iteration}:${entry.occurred_at}:${index}`}
            entry={entry}
            onArtifact={(artifactId) => void openArtifact(artifactId)}
          />
        ))}
      </View>

      <View style={styles.row}>
        {task.state === "running" || task.state === "waiting_approval" || task.state === "queued" ? (
          <>
            <Action label="暂停" onPress={() => void command("pause")} />
            <Action label="停止" danger onPress={() => void command("cancel")} />
          </>
        ) : null}
        {task.state === "paused" ? <Action label="继续" onPress={() => void command("resume")} /> : null}
        {task.state === "completed" || task.state === "failed" || task.state === "cancelled" ? (
          <Action label="重新执行" onPress={() => void command("retry")} />
        ) : null}
      </View>
    </ScrollView>
  );
}

function pendingApproval(events: TaskEvent[]): ApprovalRequest | null {
  let pending: ApprovalRequest | null = null;
  for (const event of events) {
    if (event.event_type === "approval_requested") {
      pending = {
        approvalId: String(event.payload.approval_id ?? ""),
        taskId: event.task_id,
        toolName: String(event.payload.tool_name ?? ""),
        reason: String(event.payload.reason ?? ""),
      };
    } else if (event.event_type === "approval_resolved") {
      pending = null;
    }
  }
  return pending?.approvalId ? pending : null;
}

function originLabel(origin: TaskSnapshot["origin"]): string {
  return ({ user: "立即执行", agent: "小诺创建", scheduled: "定时执行", event: "事件启动" })[origin];
}

function stateLabel(state: TaskSnapshot["state"]): string {
  return ({ queued: "排队中", running: "进行中", waiting_approval: "待确认", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" })[state];
}

function TraceEntry({
  entry,
  onArtifact,
}: {
  entry: TaskTraceEntry;
  onArtifact(artifactId: string): void;
}) {
  const content = entry.content;
  if (entry.entry_type === "reasoning") {
    return content ? <Text style={styles.reasoning}>{content}</Text> : null;
  }
  if (entry.entry_type === "tool_call") {
    return <Text style={styles.toolLine}>⌁ {entry.tool_name || "工具"}</Text>;
  }
  if (entry.entry_type === "tool_result") {
    return <Text style={styles.toolResult}>✓ {entry.tool_name || "完成"}</Text>;
  }
  if (entry.entry_type === "warning") {
    return <Text style={styles.warning}>{content}</Text>;
  }
  if (entry.entry_type === "artifact") {
    const artifactId = entry.artifact?.artifact_id ?? "";
    return artifactId ? (
      <Pressable onPress={() => onArtifact(artifactId)}>
        <Text style={styles.artifact}>附件 · 点击查看</Text>
      </Pressable>
    ) : null;
  }
  if (entry.entry_type === "content" || entry.entry_type === "plan") {
    return content ? <AppMarkdown value={content} style={styles.markdown} /> : null;
  }
  return null;
}

function Action({ label, danger = false, onPress }: { label: string; danger?: boolean; onPress(): void }) {
  return (
    <Pressable style={[styles.action, danger && styles.actionDanger]} onPress={onPress}>
      <Text style={[styles.actionText, danger && styles.actionDangerText]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center" },
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  summary: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 8 },
  summaryHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  origin: { color: colors.ink, fontWeight: "700", fontSize: 13 },
  state: { color: colors.accent, fontWeight: "700", fontSize: 13 },
  goal: { color: colors.ink, fontSize: 19, lineHeight: 28, fontWeight: "600" },
  phase: { color: colors.muted },
  approval: { padding: 18, borderRadius: 18, backgroundColor: "#FFF4DE", borderWidth: 1, borderColor: "#E8C886", gap: 8 },
  approvalTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  tool: { color: colors.accent, fontFamily: "monospace" },
  reason: { color: colors.ink, lineHeight: 22 },
  row: { flexDirection: "row", gap: 10 },
  approve: { flex: 1, alignItems: "center", backgroundColor: colors.accent, padding: 12, borderRadius: 12 },
  approveText: { color: "white", fontWeight: "700" },
  deny: { flex: 1, alignItems: "center", backgroundColor: colors.surface, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: colors.line },
  denyText: { color: colors.ink, fontWeight: "600" },
  timeline: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 10 },
  markdown: { width: "100%", alignSelf: "stretch" },
  reasoning: { color: colors.muted, lineHeight: 22 },
  toolLine: { color: colors.accent, fontFamily: "monospace" },
  toolResult: { color: colors.muted, fontFamily: "monospace" },
  warning: { color: colors.warning },
  artifact: { color: colors.accent, fontWeight: "600" },
  final: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line },
  action: { flex: 1, alignItems: "center", backgroundColor: colors.accentSoft, padding: 13, borderRadius: 13 },
  actionText: { color: colors.accent, fontWeight: "700" },
  actionDanger: { backgroundColor: "#F4DEDC" },
  actionDangerText: { color: colors.danger },
});
