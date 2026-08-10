import { router, useLocalSearchParams } from "expo-router";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { ChatArtifact, TaskApproval, TaskExecution, TaskTraceEntry } from "@/api/models";
import type { ResolvedArtifactFile } from "@/api/chatArtifacts";
import { AppMarkdown } from "@/components/AppMarkdown";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function TaskExecutionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const executionId = String(id ?? "");
  const gateway = useGateway();
  const [execution, setExecution] = useState<TaskExecution | null>(null);
  const [imagePreview, setImagePreview] = useState<ResolvedArtifactFile | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!gateway.client || !executionId) return;
    setError("");
    try {
      const snapshot = await gateway.runAuthenticated((client) => client.getTaskExecution(executionId));
      setExecution(snapshot);
    } catch {
      setError("执行详情加载失败，请稍后重试");
    }
  }, [executionId, gateway.client, gateway.runAuthenticated]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const event = gateway.latestEvent?.event;
    if (!event || event.task_id !== executionId) return;
    void gateway.runAuthenticated((client) => client.getTaskExecution(executionId)).then(setExecution);
  }, [executionId, gateway.latestEvent, gateway.runAuthenticated]);

  const approvals = useMemo(
    () => execution?.approvals.filter((item) => item.state === "pending") ?? [],
    [execution?.approvals],
  );
  const timeline = useMemo(
    () => (execution?.trace?.entries ?? []).filter(
      (entry) => entry.entry_type !== "final_output"
        && (entry.entry_type !== "content" || !execution?.final_result),
    ),
    [execution?.final_result, execution?.trace?.entries],
  );

  async function command(action: "cancel" | "pause" | "resume" | "rerun") {
    if (!execution || working) return;
    setWorking(true);
    setError("");
    try {
      const next = await gateway.runAuthenticated((client) => client.taskExecutionCommand(execution.execution_id, action));
      if (action === "rerun" && next) {
        router.replace(`/task-executions/${next.execution_id}`);
        return;
      }
      await refresh();
    } catch {
      setError("操作失败，请刷新状态后重试");
    } finally {
      setWorking(false);
    }
  }

  async function openArtifact(artifact: ChatArtifact) {
    try {
      const downloaded = await gateway.runAuthenticated((client) => client.downloadArtifact(
        gateway.sessionHandle,
        artifact.artifact_id,
      ));
      const safeName = downloaded.name.replace(/[^\p{L}\p{N}._ -]/gu, "_");
      const file = new File(Paths.cache, `${artifact.artifact_id}-${safeName}`);
      file.create({ overwrite: true, intermediates: true });
      file.write(downloaded.bytes);
      const resolved = { uri: file.uri, name: downloaded.name, mediaType: downloaded.mediaType };
      if (downloaded.mediaType.startsWith("image/")) setImagePreview(resolved);
      else await Sharing.shareAsync(file.uri, { mimeType: downloaded.mediaType });
    } catch {
      setError("附件暂时无法打开，请重试");
    }
  }

  function confirmDelete() {
    if (!execution || !isTerminal(execution.state)) return;
    Alert.alert("删除这次执行记录？", "任务本身和其他执行记录不会受影响。", [
      { text: "取消", style: "cancel" },
      { text: "删除", style: "destructive", onPress: () => void deleteExecution() },
    ]);
  }

  async function deleteExecution() {
    if (!execution) return;
    setWorking(true);
    try {
      await gateway.runAuthenticated((client) => client.deleteTaskExecution(execution.execution_id));
      router.replace(`/tasks/${execution.task_id}`);
    } catch {
      setError("执行记录删除失败，请重试");
      setWorking(false);
    }
  }

  if (!execution && !error) return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;
  if (!execution) return (
    <View style={styles.loading}>
      <Text style={styles.error}>{error}</Text>
      <Pressable onPress={() => void refresh()}><Text style={styles.link}>重新加载</Text></Pressable>
    </View>
  );

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.summary}>
        <View style={styles.summaryHeader}>
          <Text style={styles.reason}>{launchReasonLabel(execution.launch_reason)}</Text>
          <Text style={styles.state}>{stateLabel(execution.state)}</Text>
        </View>
        <Text style={styles.goal}>{execution.goal_snapshot}</Text>
        {execution.phase ? <Text style={styles.phase}>{execution.phase}</Text> : null}
        <Text style={styles.snapshot}>基于任务版本 {execution.task_revision} 的不可变快照</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {execution.final_result ? (
        <View style={styles.final}><AppMarkdown value={execution.final_result} style={styles.markdown} /></View>
      ) : null}
      {execution.failure_code ? (
        <View style={styles.failure}><Text style={styles.failureTitle}>本次执行未完成</Text><Text style={styles.failureText}>{execution.failure_code}</Text></View>
      ) : null}

      {approvals.map((approval, index) => (
        <View key={approval.approval_id} style={styles.approval}>
          <Text style={styles.approvalTitle}>需要你的确认{approvals.length > 1 ? ` · ${index + 1}/${approvals.length}` : ""}</Text>
          <Text style={styles.tool}>{approval.tool_name}</Text>
          <ApprovalDetails approval={approval} />
          <View style={styles.row}>
            <Action label="取消" onPress={() => void resolveApproval(approval, false)} disabled={working} />
            <Action label="确认" primary onPress={() => void resolveApproval(approval, true)} disabled={working} />
          </View>
        </View>
      ))}

      {timeline.length ? (
        <View style={styles.timeline}>
          <Text style={styles.sectionTitle}>关键步骤</Text>
          {timeline.map((entry, index) => (
            <TraceEntry key={`${entry.entry_type}:${entry.occurred_at}:${index}`} entry={entry} onArtifact={(artifact) => void openArtifact(artifact)} />
          ))}
        </View>
      ) : null}

      <View style={styles.row}>
        {execution.state === "running" || execution.state === "waiting_approval" || execution.state === "queued" ? (
          <>
            <Action label="暂停" onPress={() => void command("pause")} disabled={working} />
            <Action label="停止" danger onPress={() => void command("cancel")} disabled={working} />
          </>
        ) : null}
        {execution.state === "paused" ? <Action label="继续" primary onPress={() => void command("resume")} disabled={working} /> : null}
        {isTerminal(execution.state) ? <Action label="按本次配置再次执行" primary onPress={() => void command("rerun")} disabled={working} /> : null}
      </View>

      {isTerminal(execution.state) ? (
        <Pressable onPress={confirmDelete} style={styles.deleteButton}><Text style={styles.deleteText}>删除这次执行记录</Text></Pressable>
      ) : null}
      <ArtifactViewer file={imagePreview} onClose={() => setImagePreview(null)} onMessage={setError} />
    </ScrollView>
  );

  async function resolveApproval(approval: TaskApproval, approved: boolean) {
    if (working) return;
    setWorking(true);
    try {
      await gateway.runAuthenticated((client) => client.resolveApproval(approval.approval_id, approved));
      await refresh();
    } catch {
      setError("确认操作提交失败，请重试");
    } finally {
      setWorking(false);
    }
  }
}

function ApprovalDetails({ approval }: { approval: TaskApproval }) {
  const [effect, risk] = approval.reason.split(":", 2);
  return (
    <View style={styles.approvalDetails}>
      <Text style={styles.approvalReason}>影响：{effectLabel(effect ?? "")}</Text>
      <Text style={styles.approvalReason}>风险：{riskLabel(risk ?? "")}</Text>
      <Text style={styles.approvalReason}>可撤销性：未保证，请确认目标和参数无误</Text>
      {Object.keys(approval.arguments).length ? (
        <Text selectable style={styles.arguments}>{JSON.stringify(approval.arguments, null, 2)}</Text>
      ) : null}
    </View>
  );
}

function effectLabel(value: string): string {
  return ({ local_write: "会修改本机内容", external_side_effect: "会影响外部系统", desktop_control: "会操作桌面", unknown: "影响范围未知" } as Record<string, string>)[value] ?? "会执行受控操作";
}

function riskLabel(value: string): string {
  return ({ low: "低", medium: "中", high: "高" } as Record<string, string>)[value] ?? "未知";
}

function isTerminal(state: TaskExecution["state"]): boolean {
  return state === "completed" || state === "failed" || state === "cancelled";
}

function launchReasonLabel(reason: TaskExecution["launch_reason"]): string {
  return ({ created: "首次执行", manual: "手动执行", scheduled: "定时执行", event: "事件启动", rerun: "按历史配置再次执行" })[reason];
}

function stateLabel(state: TaskExecution["state"]): string {
  return ({ queued: "排队中", running: "进行中", waiting_approval: "待确认", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" })[state];
}

function TraceEntry({ entry, onArtifact }: { entry: TaskTraceEntry; onArtifact(artifact: ChatArtifact): void }) {
  if (entry.entry_type === "reasoning") return entry.content ? <Text style={styles.reasoning}>{entry.content}</Text> : null;
  if (entry.entry_type === "tool_call") return <Text style={styles.toolLine}>⌁ {entry.tool_name || "工具"}</Text>;
  if (entry.entry_type === "tool_result") return <Text style={styles.toolResult}>✓ {entry.tool_name || "完成"}</Text>;
  if (entry.entry_type === "warning") return <Text style={styles.warning}>{entry.content}</Text>;
  if (entry.entry_type === "artifact") {
    const artifact = entry.artifact;
    return artifact ? <Pressable onPress={() => onArtifact(artifact)}><Text style={styles.artifact}>附件 · 点击查看</Text></Pressable> : null;
  }
  if (entry.entry_type === "content" || entry.entry_type === "plan") return entry.content ? <AppMarkdown value={entry.content} style={styles.markdown} /> : null;
  return null;
}

function Action({ label, primary = false, danger = false, disabled = false, onPress }: { label: string; primary?: boolean; danger?: boolean; disabled?: boolean; onPress(): void }) {
  return (
    <Pressable disabled={disabled} style={[styles.action, primary && styles.actionPrimary, danger && styles.actionDanger, disabled && styles.disabled]} onPress={onPress}>
      <Text style={[styles.actionText, primary && styles.actionPrimaryText, danger && styles.actionDangerText]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 24 },
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  summary: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 8 },
  summaryHeader: { flexDirection: "row", justifyContent: "space-between" },
  reason: { color: colors.ink, fontWeight: "700" },
  state: { color: colors.accent, fontWeight: "700" },
  goal: { color: colors.ink, fontSize: 18, lineHeight: 27, fontWeight: "600" },
  phase: { color: colors.muted },
  snapshot: { color: colors.muted, fontSize: 12 },
  final: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line },
  markdown: { width: "100%", alignSelf: "stretch" },
  failure: { padding: 18, borderRadius: 18, backgroundColor: "#FCE9E7", gap: 6 },
  failureTitle: { color: colors.danger, fontWeight: "700" },
  failureText: { color: colors.ink },
  approval: { padding: 18, borderRadius: 18, backgroundColor: "#FFF4DE", borderWidth: 1, borderColor: "#E8C886", gap: 8 },
  approvalTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  tool: { color: colors.accent, fontFamily: "monospace" },
  approvalReason: { color: colors.ink, lineHeight: 22 },
  approvalDetails: { gap: 5 },
  arguments: { color: colors.ink, fontFamily: "monospace", fontSize: 12, backgroundColor: colors.surface, borderRadius: 10, padding: 10 },
  row: { flexDirection: "row", gap: 10 },
  timeline: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 10 },
  sectionTitle: { color: colors.ink, fontWeight: "700", fontSize: 17, marginBottom: 4 },
  reasoning: { color: colors.muted, lineHeight: 22 },
  toolLine: { color: colors.accent, fontFamily: "monospace" },
  toolResult: { color: colors.muted, fontFamily: "monospace" },
  warning: { color: colors.warning },
  artifact: { color: colors.accent, fontWeight: "600" },
  action: { flex: 1, minHeight: 46, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft, borderRadius: 13, paddingHorizontal: 10 },
  actionText: { color: colors.accent, fontWeight: "700", textAlign: "center" },
  actionPrimary: { backgroundColor: colors.accent },
  actionPrimaryText: { color: "white" },
  actionDanger: { backgroundColor: "#F4DEDC" },
  actionDangerText: { color: colors.danger },
  disabled: { opacity: 0.45 },
  error: { color: colors.danger, lineHeight: 21 },
  link: { color: colors.accent, fontWeight: "700" },
  deleteButton: { alignItems: "center", padding: 14, marginTop: 8 },
  deleteText: { color: colors.danger, fontWeight: "600" },
});
