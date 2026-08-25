import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { AsyncStateView } from "@/components/AsyncStateView";
import type { AgentSummary, Task, TaskExecution, TaskState } from "@/api/models";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { useGateway } from "@/state/GatewayProvider";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { blockedPreflightMessages, warningPreflightMessages } from "@/components/preflightPresentation";
import { useI18n } from "@/i18n";
import { AppPressable } from "@/components/AppPressable";
import { loadTaskDetailCache, storeTaskDetailCache } from "@/storage/taskDetailCache";

export default function TaskDetailScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const taskId = String(params.id ?? "");
  const gateway = useGateway();
  const { unreadExecutionIds } = useTaskReminders();
  const { t } = useI18n();
  const [task, setTask] = useState<Task | null>(null);
  const [executions, setExecutions] = useState<TaskExecution[]>([]);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const latestRefreshEvent = useRef(gateway.latestEvent?.feed_event_id ?? 0);

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
      void storeTaskDetailCache(taskId, { task: nextTask, executions: nextExecutions });
    } catch {
      setError(t("taskDetail.loadFailed"));
    }
  }, [gateway.client, gateway.runAuthenticated, t, taskId]);

  useEffect(() => {
    let active = true;
    void loadTaskDetailCache(taskId).then((cached) => {
      if (!active || !cached) return;
      setTask(cached.task);
      setExecutions(cached.executions);
    }).finally(() => {
      if (active) void refresh();
    });
    return () => { active = false; };
  }, [refresh, taskId]);
  useEffect(() => {
    if (!gateway.latestEvent || gateway.latestEvent.feed_event_id <= latestRefreshEvent.current) return;
    latestRefreshEvent.current = gateway.latestEvent.feed_event_id;
    const timer = setTimeout(() => void refresh(), 250);
    return () => clearTimeout(timer);
  }, [gateway.latestEvent, refresh]);

  async function executeNow() {
    if (!task || working) return;
    setWorking("execute");
    setError("");
    try {
      const preflight = await gateway.runAuthenticated((client) => client.preflightTask(task.task_id));
      if (!preflight.ready) {
        const blocked = blockedPreflightMessages(preflight.checks).join("；");
        setError(blocked || t("taskDetail.executeFailed"));
        return;
      }
      // Warnings are a user decision, not a silent override: the run only
      // starts after the user acknowledges them.
      const warnings = warningPreflightMessages(preflight.checks);
      if (warnings.length) {
        const proceed = await new Promise<boolean>((resolve) => {
          Alert.alert(
            t("taskDetail.preflightConfirmTitle"),
            warnings.join("；"),
            [
              { text: t("common.cancel"), style: "cancel", onPress: () => resolve(false) },
              { text: t("taskDetail.preflightContinue"), onPress: () => resolve(true) },
            ],
            { cancelable: true, onDismiss: () => resolve(false) },
          );
        });
        if (!proceed) return;
      }
      const execution = await gateway.runAuthenticated((client) => client.executeTask(task.task_id));
      router.push(`/task-executions/${execution.execution_id}`);
    } catch {
      setError(t("taskDetail.executeFailed"));
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
      setError(t("taskDetail.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  function confirmDelete() {
    if (!task) return;
    const hasActive = executions.some((item) => !isTerminal(item.state));
    Alert.alert(
      t("taskDetail.deleteTitle"),
      hasActive
        ? t("taskDetail.deleteActive")
        : t("taskDetail.deleteBody", { title: task.title, count: executions.length }),
      hasActive ? [{ text: t("common.gotIt") }] : [
        { text: t("common.cancel"), style: "cancel" },
        {
          text: t("common.delete"),
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
      router.back();
    } catch {
      setError(t("taskDetail.deleteFailed"));
      setWorking("");
    }
  }

  if (!task && !error && gateway.status === "ready") {
    return <View style={styles.loading}><AsyncStateView state="loading" /></View>;
  }

  if (!task) {
    return (
      <View style={styles.loading}>
        <AsyncStateView
          state="error"
          message={error || t("chat.reconnecting")}
          retryLabel={t("tasks.reload")}
          onRetry={() => void refresh()}
        />
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.summary}>
        <View style={styles.summaryHeader}>
        <Text style={styles.state}>{taskStateLabel(task.state, t)}</Text>
          <Text style={styles.revision}>{t("taskDetail.revision", { revision: task.revision })}</Text>
        </View>
        <Text style={styles.title}>{task.title}</Text>
        <Text style={styles.goal}>{task.goal}</Text>
        <Text style={styles.policy}>{agentName(task.agent_id, gateway.agents)} · {launchLabel(task, t)} · {t("tasks.executions", { count: task.execution_count })}</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.actions}>
        <Action icon="play" label={t("taskDetail.executeNow")} onPress={() => void executeNow()} disabled={Boolean(working)} busy={working === "execute"} primary />
        <Action icon="edit" label={t("taskDetail.edit")} onPress={() => router.push(`/tasks/${task.task_id}/edit`)} disabled={Boolean(working)} />
      </View>
      <View style={styles.actions}>
        {task.state === "active" ? <Action icon="pause" label={t("taskDetail.pause")} onPress={() => void setState("pause")} disabled={Boolean(working)} busy={working === "pause"} /> : null}
        {task.state === "paused" ? <Action icon="play" label={t("taskDetail.resume")} onPress={() => void setState("resume")} disabled={Boolean(working)} busy={working === "resume"} /> : null}
        {task.state === "archived" ? <Action icon="restore" label={t("taskDetail.restore")} onPress={() => void setState("restore")} disabled={Boolean(working)} busy={working === "restore"} /> : null}
        {task.state !== "archived" ? <Action icon="archive" label={t("taskDetail.archive")} onPress={() => void setState("archive")} disabled={Boolean(working)} busy={working === "archive"} /> : null}
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>{t("taskDetail.executions")}</Text>
        <Text style={styles.sectionCount}>{executions.length}</Text>
      </View>
      {executions.length ? executions.map((execution) => (
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={`${launchReasonLabel(execution.launch_reason, t)}，${executionStatusLabel(execution, t)}${unreadExecutionIds.has(execution.execution_id) ? `，${t("reminders.unread")}` : ""}`}
          key={execution.execution_id}
          style={styles.execution}
          onPress={() => router.push(`/task-executions/${execution.execution_id}`)}
        >
          <View style={styles.executionHeader}>
            <View style={styles.executionTitleRow}>
              {unreadExecutionIds.has(execution.execution_id) ? <View accessibilityElementsHidden style={styles.unreadDot} /> : null}
              <Text style={styles.executionTitle}>{launchReasonLabel(execution.launch_reason, t)}</Text>
            </View>
            <Text style={styles.executionState}>{executionStatusLabel(execution, t)}</Text>
          </View>
          {execution.final_result ? <Text style={styles.result} numberOfLines={2}>{execution.final_result}</Text> : null}
          {execution.failure_code ? <Text style={styles.failure}>{t("taskDetail.incomplete", { code: execution.failure_code })}</Text> : null}
          <Text style={styles.time}>{new Date(execution.created_at * 1000).toLocaleString()}</Text>
        </AppPressable>
      )) : (
        <View style={styles.empty}><Text style={styles.emptyText}>{t("taskDetail.noExecutions")}</Text></View>
      )}

      <AppPressable accessibilityRole="button" disabled={Boolean(working)} onPress={confirmDelete} style={styles.deleteButton}>
        {working === "delete"
          ? <ActivityIndicator color={colors.danger} size="small" />
          : <View style={styles.deleteContent}><AppIcon name="trash" color={colors.danger} size={18} /><Text style={styles.deleteText}>{t("taskDetail.deleteAll")}</Text></View>}
      </AppPressable>
    </ScrollView>
  );
}

function isTerminal(state: TaskState): boolean {
  return state === "completed" || state === "failed" || state === "cancelled";
}

function agentName(agentId: string, agents: AgentSummary[]): string {
  return agents.find((agent) => agent.agent_id === agentId)?.display_name ?? agentId;
}

function taskStateLabel(state: Task["state"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({ active: t("tasks.state.active"), paused: t("tasks.state.paused"), archived: t("tasks.state.archived") })[state];
}

function executionStateLabel(state: TaskState, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ queued: t("taskState.queued"), running: t("taskState.running"), waiting_approval: t("taskState.waitingApproval"), paused: t("tasks.state.paused"), completed: t("taskState.completed"), failed: t("taskState.failed"), cancelled: t("taskState.cancelled") })[state];
}

function executionStatusLabel(execution: TaskExecution, t: ReturnType<typeof useI18n>["t"]): string {
  const status = execution.work_status?.status;
  if (!status) return executionStateLabel(execution.state, t);
  return ({ queued: t("taskState.queued"), working: t("taskState.running"), waiting_for_you: t("taskState.waitingApproval"), completed: t("taskState.completed"), failed: t("taskState.failed"), paused: t("tasks.state.paused"), cancelled: t("taskState.cancelled") })[status];
}

function launchLabel(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ immediate: t("taskDetail.launchImmediate"), scheduled: t("tasks.launch.scheduled"), event: t("tasks.launch.event") })[task.launch_policy.kind];
}

function launchReasonLabel(reason: TaskExecution["launch_reason"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({ created: t("taskDetail.reason.created"), manual: t("taskDetail.reason.manual"), scheduled: t("taskDetail.reason.scheduled"), event: t("taskDetail.reason.event"), rerun: t("taskDetail.reason.rerun"), follow_up: t("taskDetail.reason.followUp") })[reason];
}

function Action({ icon, label, primary = false, disabled = false, busy = false, onPress }: { icon: AppIconName; label: string; primary?: boolean; disabled?: boolean; busy?: boolean; onPress(): void }) {
  return (
    <AppPressable disabled={disabled} style={[styles.action, primary && styles.actionPrimary, disabled && styles.disabled]} onPress={onPress}>
      {busy
        ? <ActivityIndicator color={primary ? colors.onAccent : colors.accent} size="small" />
        : <><AppIcon name={icon} color={primary ? colors.onAccent : colors.accent} size={18} /><Text style={[styles.actionText, primary && styles.actionPrimaryText]}>{label}</Text></>}
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.medium, padding: spacing.xlarge },
  container: { padding: spacing.large, gap: spacing.large, paddingBottom: 48 },
  summary: { backgroundColor: colors.surface, borderRadius: radii.large, padding: spacing.xlarge, borderWidth: 1, borderColor: colors.line, gap: spacing.small , ...shadows.card },
  summaryHeader: { flexDirection: "row", justifyContent: "space-between" },
  state: { color: colors.accent, fontWeight: "700" },
  revision: { color: colors.muted, fontSize: 12 },
  title: { color: colors.ink, fontSize: 21, lineHeight: 28, fontWeight: "700" },
  goal: { color: colors.ink, fontSize: 16, lineHeight: 24 },
  policy: { color: colors.muted, marginTop: spacing.xsmall },
  actions: { flexDirection: "row", gap: spacing.medium },
  action: { flex: 1, minHeight: 44, flexDirection: "row", gap: spacing.small, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accentSoft },
  actionPrimary: { backgroundColor: colors.accent },
  actionText: { color: colors.accent, fontWeight: "700" },
  actionPrimaryText: { color: colors.onAccent },
  disabled: { opacity: 0.45 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: spacing.small },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "700" },
  sectionCount: { color: colors.muted },
  execution: { padding: spacing.large, backgroundColor: colors.surface, borderRadius: radii.large, borderWidth: 1, borderColor: colors.line, gap: spacing.small , ...shadows.card },
  executionHeader: { flexDirection: "row", justifyContent: "space-between" },
  executionTitleRow: { flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: spacing.small },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.danger },
  executionTitle: { color: colors.ink, fontWeight: "700" },
  executionState: { color: colors.accent, fontWeight: "600" },
  result: { color: colors.ink, lineHeight: 21 },
  failure: { color: colors.danger },
  time: { color: colors.muted, fontSize: 12 },
  empty: { padding: spacing.xlarge, borderRadius: radii.large, backgroundColor: colors.surface, alignItems: "center" },
  emptyText: { color: colors.muted },
  error: { color: colors.danger, lineHeight: 21 },
  link: { color: colors.accent, fontWeight: "700" },
  deleteButton: { alignItems: "center", padding: spacing.large, marginTop: spacing.medium },
  deleteContent: { flexDirection: "row", alignItems: "center", gap: spacing.small },
  deleteText: { color: colors.danger, fontWeight: "600" },
});
