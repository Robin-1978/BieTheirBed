import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  RefreshControl,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { Task, TaskDefinitionState, TaskState } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { PrimarySwipeNavigation } from "@/components/PrimarySwipeNavigation";
import { currentTaskSections } from "@/components/taskListPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { colors } from "@/theme";
import { loadOfflineTasks, removeOfflineTask, type QueuedTask } from "@/storage/offlineTaskQueue";
import { loadTaskCache, storeTaskCache } from "@/storage/taskCache";

type Filter = "current" | TaskDefinitionState;
type TaskSection = { key: string; title: string; data: Task[] };

export default function TasksScreen() {
  const gateway = useGateway();
  const { unreadTaskIds } = useTaskReminders();
  const { t } = useI18n();
  const filters: Array<{ label: string; value: Filter }> = [
    { label: t("tasks.filter.current"), value: "current" },
    { label: t("tasks.filter.active"), value: "active" },
    { label: t("tasks.filter.paused"), value: "paused" },
    { label: t("tasks.filter.archived"), value: "archived" },
  ];
  const [tasks, setTasks] = useState<Task[]>([]);
  const [filter, setFilter] = useState<Filter>("current");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const taskCacheScope = gateway.nodeId || "unselected";
  const [queued, setQueued] = useState<QueuedTask[]>([]);
  const latestRefreshEvent = useRef(gateway.latestEvent?.feed_event_id ?? 0);

  const refresh = useCallback(async () => {
    if (!gateway.client) return;
    setRefreshing(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.listTasks({
        includeArchived: true,
        limit: 200,
      }));
      setTasks(result.tasks);
      void storeTaskCache(taskCacheScope, result.tasks);
    } catch {
      setError(t("tasks.loadFailed"));
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, t, taskCacheScope]);

  useEffect(() => {
    let active = true;
    void loadTaskCache(taskCacheScope).then((cached) => {
      if (!active || !cached) return;
      setTasks(cached);
      setLoading(false);
    }).finally(() => {
      if (active) void refresh();
    });
    return () => { active = false; };
  }, [refresh, taskCacheScope]);
  useEffect(() => { void loadOfflineTasks().then(setQueued); }, []);
  const flushQueued = useCallback(async () => {
    if (!gateway.client || gateway.status !== "ready") return;
    for (const item of queued) {
      try {
        await gateway.runAuthenticated((client) => client.createTask({
          title: item.title,
          goal: item.goal,
          notificationPolicy: item.notificationPolicy,
          launchPolicy: item.launchPolicy as never,
          agentId: item.agentId,
          clientRequestId: item.clientRequestId,
        }));
        await removeOfflineTask(item.queueId);
      } catch {
        break;
      }
    }
    setQueued(await loadOfflineTasks());
    await refresh();
  }, [gateway.client, gateway.runAuthenticated, gateway.status, queued, refresh]);
  useEffect(() => { if (gateway.status === "ready" && queued.length) void flushQueued(); }, [flushQueued, gateway.status, queued.length]);
  useEffect(() => {
    if (!gateway.latestEvent || gateway.latestEvent.feed_event_id <= latestRefreshEvent.current) return;
    latestRefreshEvent.current = gateway.latestEvent.feed_event_id;
    const timer = setTimeout(() => void refresh(), 250);
    return () => clearTimeout(timer);
  }, [gateway.latestEvent, refresh]);

  const visibleTasks = useMemo(
    () => tasks.filter((task) => filter === "current" ? task.state !== "archived" : task.state === filter),
    [filter, tasks],
  );
  const sections = useMemo<TaskSection[]>(() => filter === "current"
    ? currentTaskSections(visibleTasks).map((section) => ({
        ...section,
        title: taskSectionTitle(section.key, t),
      }))
    : [{ key: filter, title: "", data: visibleTasks }], [filter, t, visibleTasks]);

  return (
    <PrimarySwipeNavigation current="tasks">
      <View style={styles.container}>
      <View style={styles.topline}>
        <View>
          <Text style={styles.heading}>{t("tasks.title")}</Text>
          <Text style={styles.description}>{t("tasks.description")}</Text>
        </View>
        <View style={styles.topActions}>
          <AppPressable
            accessibilityRole="button"
            accessibilityLabel={t("tasks.new")}
            onPress={() => router.push("/tasks/new")}
            style={styles.newButton}
          >
            <AppIcon name="plus" color={colors.white} size={22} />
          </AppPressable>
        </View>
      </View>
      {gateway.availableUpdate ? (
        <AppPressable style={styles.updateBanner} onPress={() => router.push("/update") }>
          <View>
            <Text style={styles.updateTitle}>{t("tasks.updateAvailable", { version: gateway.availableUpdate.version_name })}</Text>
            <Text style={styles.updateDetail}>{t("tasks.updateResume")}</Text>
          </View>
          <Text style={styles.updateLink}>{t("tasks.view")}</Text>
        </AppPressable>
      ) : null}
      {queued.length ? <AppPressable style={styles.offlineBanner} onPress={() => void flushQueued()}><Text style={styles.offlineTitle}>{t("tasks.offlineQueued", { count: queued.length })}</Text><Text style={styles.offlineDetail}>{t("tasks.offlineQueuedDetail")}</Text></AppPressable> : null}
      <View style={styles.filters}>
        {filters.map((item) => (
          <AppPressable
            key={item.value}
            accessibilityRole="button"
            accessibilityState={{ selected: filter === item.value }}
            onPress={() => setFilter(item.value)}
            style={[styles.filter, filter === item.value && styles.filterActive]}
          >
            <Text style={[styles.filterText, filter === item.value && styles.filterTextActive]}>{item.label}</Text>
          </AppPressable>
        ))}
      </View>
      {loading ? <AsyncStateView state="loading" /> : null}
      {error ? <AsyncStateView state="error" message={error} retryLabel={t("tasks.reload")} onRetry={() => void refresh()} /> : null}
      <SectionList
        sections={sections}
        keyExtractor={(task) => task.task_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh()} />}
        contentContainerStyle={styles.list}
        ListEmptyComponent={!loading && !error ? (
          <AsyncStateView state="empty" title={t("tasks.emptyTitle")} message={t("tasks.emptyBody")} />
        ) : null}
        renderSectionHeader={({ section }) => section.title ? (
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{section.title}</Text>
            <Text style={styles.sectionCount}>{section.data.length}</Text>
          </View>
        ) : null}
        renderItem={({ item }) => (
          <AppPressable
            accessibilityRole="button"
            accessibilityLabel={`${item.title}，${taskStatusLabel(item, t)}，${t("tasks.executions", { count: item.execution_count })}${unreadTaskIds.has(item.task_id) ? `，${t("reminders.unread")}` : ""}`}
            style={styles.task}
            onPress={() => router.push(`/tasks/${item.task_id}`)}
          >
            <View style={styles.taskHeader}>
              <View style={styles.titleRow}>
                {unreadTaskIds.has(item.task_id) ? <View accessibilityElementsHidden style={styles.unreadDot} /> : null}
                <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
              </View>
              <Text style={[
                styles.state,
                taskStatusTone(item) === "warning" && styles.warningState,
                taskStatusTone(item) === "danger" && styles.dangerState,
              ]}>{taskStatusLabel(item, t)}</Text>
            </View>
            {item.latest_execution_state === "failed" && item.latest_execution_failure_code ? (
              <Text style={styles.failure} numberOfLines={2}>{t("tasks.latestFailure", { code: item.latest_execution_failure_code })}</Text>
            ) : item.latest_execution_summary ? (
              <View style={styles.latestResult}>
                <Text style={styles.latestLabel}>{t("tasks.latestResult")}</Text>
                <Text style={styles.result} numberOfLines={3}>{item.latest_execution_summary}</Text>
              </View>
            ) : item.latest_execution_failure_code ? (
              <Text style={styles.failure} numberOfLines={2}>{t("tasks.latestFailure", { code: item.latest_execution_failure_code })}</Text>
            ) : (
              <Text style={styles.goal} numberOfLines={3}>{item.goal}</Text>
            )}
            <View style={styles.metaRow}>
              <View style={styles.metaCopy}>
                <Text style={styles.meta}>{launchLabel(item, t)}</Text>
                <Text style={styles.meta}>{t("tasks.executions", { count: item.execution_count })}</Text>
                {item.state !== "active" ? <Text style={styles.meta}>{stateLabel(item.state, t)}</Text> : null}
              </View>
              <AppIcon name="chevron-right" color={colors.muted} size={18} />
            </View>
          </AppPressable>
        )}
      />
      </View>
    </PrimarySwipeNavigation>
  );
}

function stateLabel(state: TaskDefinitionState, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ active: t("tasks.state.active"), paused: t("tasks.state.paused"), archived: t("tasks.state.archived") })[state];
}

function taskSectionTitle(key: "needs_action" | "in_progress" | "recent" | "not_started", t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    needs_action: t("tasks.section.needs_action"),
    in_progress: t("tasks.section.in_progress"),
    recent: t("tasks.section.recent"),
    not_started: t("tasks.section.not_started"),
  })[key];
}

function taskStatusLabel(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  if (task.work_status) {
    return userWorkStatusLabel(task.work_status.status, t);
  }
  if (task.pending_approval_count > 0 || task.latest_execution_state === "waiting_approval") {
    return t("taskState.waitingApproval");
  }
  if (task.latest_execution_state) return executionStateLabel(task.latest_execution_state, t);
  return stateLabel(task.state, t);
}

function userWorkStatusLabel(status: NonNullable<Task["work_status"]>["status"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    queued: t("taskState.queued"),
    working: t("taskState.running"),
    waiting_for_you: t("taskState.waitingApproval"),
    completed: t("taskState.completed"),
    failed: t("taskState.failed"),
    paused: t("tasks.state.paused"),
    cancelled: t("taskState.cancelled"),
  })[status];
}

function executionStateLabel(state: TaskState, t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    queued: t("taskState.queued"),
    running: t("taskState.running"),
    waiting_approval: t("taskState.waitingApproval"),
    paused: t("tasks.state.paused"),
    completed: t("taskState.completed"),
    failed: t("taskState.failed"),
    cancelled: t("taskState.cancelled"),
  })[state];
}

function taskStatusTone(task: Task): "normal" | "warning" | "danger" {
  if (task.work_status) {
    if (task.work_status.status === "waiting_for_you" || task.work_status.status === "paused") return "warning";
    if (task.work_status.status === "failed") return "danger";
    return "normal";
  }
  if (task.pending_approval_count > 0 || task.latest_execution_state === "waiting_approval") return "warning";
  if (task.latest_execution_state === "failed") return "danger";
  if (task.latest_execution_state === "paused" || task.state === "paused") return "warning";
  return "normal";
}

function launchLabel(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  if (task.launch_policy.kind === "scheduled") return t("tasks.launch.scheduled");
  if (task.launch_policy.kind === "event") return t("tasks.launch.event");
  return t("tasks.launch.manual");
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  topline: { padding: 18, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  heading: { color: colors.ink, fontSize: 24, fontWeight: "700" },
  description: { color: colors.muted, marginTop: 4, fontSize: 13 },
  newButton: { width: 42, height: 42, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center", borderRadius: 14 },
  topActions: { flexDirection: "row", alignItems: "center", gap: 14 },
  updateBanner: { marginHorizontal: 16, marginBottom: 14, padding: 14, borderRadius: 16, backgroundColor: colors.accentSoft, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  updateTitle: { color: colors.ink, fontWeight: "700" },
  updateDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  updateLink: { color: colors.accent, fontWeight: "700" },
  offlineBanner: { marginHorizontal: 16, marginBottom: 14, padding: 14, borderRadius: 16, backgroundColor: colors.warningSoft, gap: 4 },
  offlineTitle: { color: colors.ink, fontWeight: "700" },
  offlineDetail: { color: colors.muted, fontSize: 12 },
  filters: { flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  filter: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 14, backgroundColor: colors.surface },
  filterActive: { backgroundColor: colors.accentSoft },
  filterText: { color: colors.muted },
  filterTextActive: { color: colors.accent, fontWeight: "600" },
  list: { padding: 16, gap: 12, flexGrow: 1 },
  sectionHeader: { marginTop: 7, marginBottom: 1, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  sectionTitle: { color: colors.ink, fontSize: 16, fontWeight: "700" },
  sectionCount: { color: colors.muted, fontSize: 12 },
  task: { padding: 16, backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, gap: 8 },
  taskHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  titleRow: { flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 8 },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.danger },
  title: { flex: 1, color: colors.ink, fontWeight: "700", fontSize: 17 },
  state: { color: colors.accent, fontWeight: "600", fontSize: 12 },
  warningState: { color: colors.warning },
  dangerState: { color: colors.danger },
  goal: { color: colors.ink, fontSize: 15, lineHeight: 22 },
  latestResult: { gap: 3 },
  latestLabel: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  result: { color: colors.ink, fontSize: 15, lineHeight: 22 },
  failure: { color: colors.danger, fontSize: 14, lineHeight: 21 },
  metaRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  metaCopy: { flexDirection: "row", flexWrap: "wrap", gap: 10, flex: 1, marginRight: 8 },
  meta: { color: colors.muted, fontSize: 12 },
});
