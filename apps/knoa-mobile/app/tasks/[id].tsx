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
import Markdown from "react-native-marked";

import type { ApprovalRequest, TaskEvent, TaskSnapshot } from "@/api/models";
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
    const [snapshot, timeline] = await Promise.all([
      gateway.client.getTask(taskId),
      gateway.client.taskEvents(taskId),
    ]);
    setTask(snapshot);
    setEvents(timeline);
  }, [gateway.client, taskId]);

  useEffect(() => {
    void refresh();
  }, [refresh, gateway.latestEvent]);

  const approval = useMemo(() => pendingApproval(events), [events]);

  async function command(action: "cancel" | "pause" | "resume" | "retry") {
    if (!gateway.client || !task) return;
    setWorking(true);
    try {
      await gateway.client.taskCommand(task.task_id, action);
      await refresh();
    } finally {
      setWorking(false);
    }
  }

  async function resolve(approved: boolean) {
    if (!gateway.client || !approval) return;
    setWorking(true);
    try {
      await gateway.client.resolveApproval(approval.approvalId, approved);
      await refresh();
    } finally {
      setWorking(false);
    }
  }

  async function openArtifact(artifactId: string) {
    if (!gateway.client) return;
    const downloaded = await gateway.client.downloadArtifact(
      gateway.sessionHandle,
      artifactId,
    );
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
        <Text style={styles.state}>{task.state.replaceAll("_", " ")}</Text>
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

      <View style={styles.timeline}>
        {events.map((event) => (
          <TimelineEvent
            key={`${event.task_id}:${event.event_seq}`}
            event={event}
            onArtifact={(artifactId) => void openArtifact(artifactId)}
          />
        ))}
      </View>

      {task.final_summary ? (
        <View style={styles.final}>
          <Markdown value={task.final_summary} flatListProps={{ scrollEnabled: false }} />
        </View>
      ) : null}

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

function TimelineEvent({
  event,
  onArtifact,
}: {
  event: TaskEvent;
  onArtifact(artifactId: string): void;
}) {
  const content = String(event.payload.content ?? "");
  if (event.event_type === "reasoning_delta") {
    return content ? <Text style={styles.reasoning}>{content}</Text> : null;
  }
  if (event.event_type === "tool_call") {
    return <Text style={styles.toolLine}>⌁ {String(event.payload.tool_name ?? "工具")}</Text>;
  }
  if (event.event_type === "tool_result") {
    return <Text style={styles.toolResult}>✓ {String(event.payload.tool_name ?? "完成")}</Text>;
  }
  if (event.event_type === "warning") {
    return <Text style={styles.warning}>{content}</Text>;
  }
  if (event.event_type === "artifact") {
    const artifactId = String(event.payload.artifact_id ?? "");
    return artifactId ? (
      <Pressable onPress={() => onArtifact(artifactId)}>
        <Text style={styles.artifact}>附件 · 点击查看</Text>
      </Pressable>
    ) : null;
  }
  if (event.event_type === "content_delta" || event.event_type === "final_output") {
    return content ? <Markdown value={content} flatListProps={{ scrollEnabled: false }} /> : null;
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
  state: { color: colors.accent, fontWeight: "700", textTransform: "uppercase", fontSize: 12 },
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
