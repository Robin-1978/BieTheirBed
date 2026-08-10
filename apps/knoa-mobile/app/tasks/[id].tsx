import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { Task, TaskExecution, TaskState } from "@/api/models";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function TaskDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const taskId = String(params.id ?? "");
  const gateway = useGateway();
  const [task, setTask] = useState<Task | null>(null);
  const [executions, setExecutions] = useState<TaskExecution[]>([]);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!gateway.client || !taskId) return;
    setError("");
    try {
      const [nextTask, nextExecutions] = await gateway.runAuthenticated((client) => Promise.all([
        client.getTask(taskId),
        client.listTaskExecutions(taskId),
      ]));
      setTask(nextTask);
      setExecutions(nextExecutions);
    } catch {
      setError("任务详情加载失败，请稍后重试");
    }
  }, [gateway.client, gateway.runAuthenticated, taskId]);

  useEffect(() => { void refresh(); }, [refresh, gateway.latestEvent]);

  async function executeNow() {
    if (!task || working) return;
    setWorking("execute");
    setError("");
    try {
      const execution = await gateway.runAuthenticated((client) => client.executeTask(task.task_id));
      router.push(`/task-executions/${execution.execution_id}`);
    } catch {
      setError("当前无法开始执行；如果已有执行正在进行，请先查看或停止它");
    } finally {
      setWorking("");
    }
  }

  async function setState(command: "pause" | "resume" | "archive" | "restore") {
    if (!task || working) return;
    setWorking(command);
    try {
      const updated = await gateway.runAuthenticated((client) => client.taskDefinitionCommand(task.task_id, command));
      setTask(updated);
    } catch {
      setError("操作失败，请重试");
    } finally {
      setWorking("");
    }
  }

  function confirmDelete() {
    if (!task) return;
    const hasActive = executions.some((item) => !isTerminal(item.state));
    Alert.alert(
      "删除任务？",
      hasActive
        ? "任务仍有执行正在进行，请先停止执行后再删除。"
        : `将删除“${task.title}”及全部 ${executions.length} 条执行记录，此操作无法撤销。`,
      hasActive ? [{ text: "知道了" }] : [
        { text: "取消", style: "cancel" },
        {
          text: "删除",
          style: "destructive",
          onPress: () => void deleteTask(),
        },
      ],
    );
  }

  async function deleteTask() {
    if (!task || working) return;
    setWorking("delete");
    try {
      await gateway.runAuthenticated((client) => client.deleteTask(task.task_id));
      router.replace("/tasks");
    } catch {
      setError("任务删除失败，请确认所有执行都已结束");
      setWorking("");
    }
  }

  if (!task && !error) {
    return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;
  }

  if (!task) {
    return (
      <View style={styles.loading}>
        <Text style={styles.error}>{error}</Text>
        <Pressable onPress={() => void refresh()}><Text style={styles.link}>重新加载</Text></Pressable>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.summary}>
        <View style={styles.summaryHeader}>
          <Text style={styles.state}>{taskStateLabel(task.state)}</Text>
          <Text style={styles.revision}>版本 {task.revision}</Text>
        </View>
        <Text style={styles.title}>{task.title}</Text>
        <Text style={styles.goal}>{task.goal}</Text>
        <Text style={styles.policy}>{launchLabel(task)} · 已执行 {task.execution_count} 次</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.actions}>
        <Action label="立即执行" onPress={() => void executeNow()} disabled={Boolean(working)} busy={working === "execute"} primary />
        <Action label="编辑" onPress={() => router.push(`/tasks/${task.task_id}/edit`)} disabled={Boolean(working)} />
      </View>
      <View style={styles.actions}>
        {task.state === "active" ? <Action label="暂停自动启动" onPress={() => void setState("pause")} disabled={Boolean(working)} busy={working === "pause"} /> : null}
        {task.state === "paused" ? <Action label="恢复启用" onPress={() => void setState("resume")} disabled={Boolean(working)} busy={working === "resume"} /> : null}
        {task.state === "archived" ? <Action label="恢复任务" onPress={() => void setState("restore")} disabled={Boolean(working)} busy={working === "restore"} /> : null}
        {task.state !== "archived" ? <Action label="归档" onPress={() => void setState("archive")} disabled={Boolean(working)} busy={working === "archive"} /> : null}
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>执行记录</Text>
        <Text style={styles.sectionCount}>{executions.length}</Text>
      </View>
      {executions.length ? executions.map((execution) => (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${launchReasonLabel(execution.launch_reason)}，${executionStateLabel(execution.state)}`}
          key={execution.execution_id}
          style={styles.execution}
          onPress={() => router.push(`/task-executions/${execution.execution_id}`)}
        >
          <View style={styles.executionHeader}>
            <Text style={styles.executionTitle}>{launchReasonLabel(execution.launch_reason)}</Text>
            <Text style={styles.executionState}>{executionStateLabel(execution.state)}</Text>
          </View>
          {execution.final_result ? <Text style={styles.result} numberOfLines={2}>{execution.final_result}</Text> : null}
          {execution.failure_code ? <Text style={styles.failure}>未完成：{execution.failure_code}</Text> : null}
          <Text style={styles.time}>{new Date(execution.created_at * 1000).toLocaleString()}</Text>
        </Pressable>
      )) : (
        <View style={styles.empty}><Text style={styles.emptyText}>还没有执行记录</Text></View>
      )}

      <Pressable accessibilityRole="button" disabled={Boolean(working)} onPress={confirmDelete} style={styles.deleteButton}>
        {working === "delete"
          ? <ActivityIndicator color={colors.danger} size="small" />
          : <Text style={styles.deleteText}>删除任务和全部记录</Text>}
      </Pressable>
    </ScrollView>
  );
}

function isTerminal(state: TaskState): boolean {
  return state === "completed" || state === "failed" || state === "cancelled";
}

function taskStateLabel(state: Task["state"]): string {
  return ({ active: "已启用", paused: "已暂停", archived: "已归档" })[state];
}

function executionStateLabel(state: TaskState): string {
  return ({ queued: "排队中", running: "进行中", waiting_approval: "待确认", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" })[state];
}

function launchLabel(task: Task): string {
  return ({ immediate: "创建时执行，之后手动启动", scheduled: "定时启动", event: "事件启动" })[task.launch_policy.kind];
}

function launchReasonLabel(reason: TaskExecution["launch_reason"]): string {
  return ({ created: "首次执行", manual: "手动执行", scheduled: "定时执行", event: "事件启动", rerun: "按历史配置再次执行" })[reason];
}

function Action({ label, primary = false, disabled = false, busy = false, onPress }: { label: string; primary?: boolean; disabled?: boolean; busy?: boolean; onPress(): void }) {
  return (
    <Pressable disabled={disabled} style={[styles.action, primary && styles.actionPrimary, disabled && styles.disabled]} onPress={onPress}>
      {busy
        ? <ActivityIndicator color={primary ? "white" : colors.accent} size="small" />
        : <Text style={[styles.actionText, primary && styles.actionPrimaryText]}>{label}</Text>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 24 },
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  summary: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 8 },
  summaryHeader: { flexDirection: "row", justifyContent: "space-between" },
  state: { color: colors.accent, fontWeight: "700" },
  revision: { color: colors.muted, fontSize: 12 },
  title: { color: colors.ink, fontSize: 21, lineHeight: 28, fontWeight: "700" },
  goal: { color: colors.ink, fontSize: 16, lineHeight: 24 },
  policy: { color: colors.muted, marginTop: 4 },
  actions: { flexDirection: "row", gap: 10 },
  action: { flex: 1, minHeight: 44, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.accentSoft },
  actionPrimary: { backgroundColor: colors.accent },
  actionText: { color: colors.accent, fontWeight: "700" },
  actionPrimaryText: { color: "white" },
  disabled: { opacity: 0.45 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 8 },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "700" },
  sectionCount: { color: colors.muted },
  execution: { padding: 16, backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, gap: 7 },
  executionHeader: { flexDirection: "row", justifyContent: "space-between" },
  executionTitle: { color: colors.ink, fontWeight: "700" },
  executionState: { color: colors.accent, fontWeight: "600" },
  result: { color: colors.ink, lineHeight: 21 },
  failure: { color: colors.danger },
  time: { color: colors.muted, fontSize: 12 },
  empty: { padding: 24, borderRadius: 16, backgroundColor: colors.surface, alignItems: "center" },
  emptyText: { color: colors.muted },
  error: { color: colors.danger, lineHeight: 21 },
  link: { color: colors.accent, fontWeight: "700" },
  deleteButton: { alignItems: "center", padding: 14, marginTop: 12 },
  deleteText: { color: colors.danger, fontWeight: "600" },
});
