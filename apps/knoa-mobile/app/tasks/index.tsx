import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
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
import { TaskBentoCard } from "@/components/TaskBentoCard";
import { currentTaskSections } from "@/components/taskListPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { loadOfflineTasks, removeOfflineTask, type QueuedTask } from "@/storage/offlineTaskQueue";
import { loadTaskCache, storeTaskCache } from "@/storage/taskCache";

type Filter = "current" | TaskDefinitionState;
type TaskSection = { key: string; title: string; data: Task[] };

export default function TasksScreen() {
  const gateway = useGateway();
  const { reminders, unreadIndexForNode, markAllRead } = useTaskReminders();
  const { t } = useI18n();

  const currentNodeUnread = unreadIndexForNode(gateway.nodeId);
  const unreadTaskIds = currentNodeUnread.taskIds;

  const unreadReminders = useMemo(() => reminders.filter((r) => !r.read), [reminders]);
  const otherNodeReminders = useMemo(
    () => unreadReminders.filter((r) => Boolean(r.nodeId && r.nodeId !== gateway.nodeId)),
    [gateway.nodeId, unreadReminders],
  );

  const [tasks, setTasks] = useState<Task[]>([]);
  const [filter, setFilter] = useState<Filter>("current");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [runningTaskId, setRunningTaskId] = useState("");
  const tasksRef = useRef<Task[]>([]);
  tasksRef.current = tasks;
  const taskCacheScope = gateway.nodeId || "unselected";
  const [queued, setQueued] = useState<QueuedTask[]>([]);
  const latestRefreshEvent = useRef(gateway.latestEvent?.feed_event_id ?? 0);

  const filters: Array<{ label: string; value: Filter; count: number }> = useMemo(() => [
    { label: t("tasks.filter.current"), value: "current", count: tasks.filter((task) => task.state !== "archived").length },
    { label: t("tasks.filter.active"), value: "active", count: tasks.filter((task) => task.state === "active").length },
    { label: t("tasks.filter.paused"), value: "paused", count: tasks.filter((task) => task.state === "paused").length },
    { label: t("tasks.filter.archived"), value: "archived", count: tasks.filter((task) => task.state === "archived").length },
  ], [t, tasks]);

  const refresh = useCallback(async () => {
    if (!gateway.client) return;
    setRefreshing(true);
    try {
      const result = await gateway.runAuthenticated((client) => client.listTasks({
        includeArchived: true,
        limit: 200,
      }));
      setTasks(result.tasks);
      setError("");
      void storeTaskCache(taskCacheScope, result.tasks);
    } catch {
      if (!tasksRef.current.length) {
        setError(t("tasks.loadFailed"));
      }
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, t, taskCacheScope]);

  useEffect(() => {
    let active = true;
    void loadTaskCache(taskCacheScope).then((cached) => {
      if (!active || !cached) return;
      setTasks((current) => current.length ? current : cached);
      setLoading(false);
    });
    return () => { active = false; };
  }, [taskCacheScope]);

  useEffect(() => {
    if (gateway.status !== "ready") return;
    void refresh();
  }, [gateway.status, refresh]);

  useEffect(() => {
    void loadOfflineTasks().then(setQueued);
  }, []);

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

  useEffect(() => {
    if (gateway.status === "ready" && queued.length) void flushQueued();
  }, [flushQueued, gateway.status, queued.length]);

  useEffect(() => {
    if (!gateway.latestEvent || gateway.latestEvent.feed_event_id <= latestRefreshEvent.current) return;
    latestRefreshEvent.current = gateway.latestEvent.feed_event_id;
    const timer = setTimeout(() => void refresh(), 250);
    return () => clearTimeout(timer);
  }, [gateway.latestEvent, refresh]);

  async function handleExecuteNow(taskId: string) {
    if (!gateway.client || runningTaskId) return;
    setRunningTaskId(taskId);
    try {
      const execution = await gateway.runAuthenticated((client) => client.executeTask(taskId));
      router.push(`/task-executions/${execution.execution_id}`);
    } catch {
      router.push(`/tasks/${taskId}`);
    } finally {
      setRunningTaskId("");
    }
  }

  async function handleTogglePause(task: Task) {
    if (!gateway.client) return;
    const command = task.state === "active" ? "pause" : "resume";
    try {
      await gateway.runAuthenticated((client) => client.taskDefinitionCommand(task.task_id, command));
      await refresh();
    } catch {
      // Refresh to keep state synced
      void refresh();
    }
  }

  async function handleArchive(task: Task) {
    if (!gateway.client) return;
    try {
      await gateway.runAuthenticated((client) => client.taskDefinitionCommand(task.task_id, "archive"));
      await refresh();
    } catch {
      void refresh();
    }
  }

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
    <View style={styles.container}>
      {/* 顶部版本与离线队列横条 */}
        {gateway.availableUpdate ? (
          <AppPressable style={styles.updateBanner} onPress={() => router.push("/update")}>
            <View style={styles.flex}>
              <Text style={styles.updateTitle}>
                {t("tasks.updateAvailable", { version: gateway.availableUpdate.version_name })}
              </Text>
              <Text style={styles.updateDetail}>{t("tasks.updateResume")}</Text>
            </View>
            <Text style={styles.updateLink}>{t("tasks.view")}</Text>
          </AppPressable>
        ) : null}

        {queued.length ? (
          <AppPressable style={styles.offlineBanner} onPress={() => void flushQueued()}>
            <Text style={styles.offlineTitle}>{t("tasks.offlineQueued", { count: queued.length })}</Text>
            <Text style={styles.offlineDetail}>{t("tasks.offlineQueuedDetail")}</Text>
          </AppPressable>
        ) : null}

        {/* 未读提醒通知与一键全读 */}
        {unreadReminders.length > 0 ? (
          <View style={styles.unreadNoticeBanner}>
            <View style={styles.unreadNoticeLeft}>
              <AppIcon name="alert" color={colors.accent} size={16} />
              <View style={styles.unreadNoticeTextWrap}>
                <Text style={styles.unreadNoticeTitle}>
                  {t("reminders.summary", { count: unreadReminders.length })}
                </Text>
                {otherNodeReminders.length > 0 ? (
                  <Text style={styles.unreadNoticeDetail}>
                    {t("reminders.otherDevices", { count: otherNodeReminders.length })}
                  </Text>
                ) : null}
              </View>
            </View>
            <AppPressable
              accessibilityRole="button"
              accessibilityLabel={t("reminders.markAllRead")}
              onPress={() => void markAllRead()}
              style={styles.markAllReadButton}
            >
              <AppIcon name="check" color={colors.accent} size={14} />
              <Text style={styles.markAllReadText}>{t("reminders.markAllRead")}</Text>
            </AppPressable>
          </View>
        ) : null}

        {/* 过滤药丸栏 */}
        <View style={styles.filters}>
          {filters.map((item) => {
            const isActive = filter === item.value;
            return (
              <AppPressable
                key={item.value}
                accessibilityRole="button"
                accessibilityState={{ selected: isActive }}
                onPress={() => setFilter(item.value)}
                style={[styles.filter, isActive && styles.filterActive]}
              >
                <Text style={[styles.filterText, isActive && styles.filterTextActive]}>
                  {item.label} ({item.count})
                </Text>
              </AppPressable>
            );
          })}
        </View>

        {loading ? <AsyncStateView state="loading" /> : null}
        {error ? (
          <AsyncStateView
            state="error"
            message={error}
            retryLabel={t("tasks.reload")}
            onRetry={() => void refresh()}
          />
        ) : null}

        <SectionList
          sections={sections}
          keyExtractor={(task) => task.task_id}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh()} />}
          contentContainerStyle={styles.list}
          ListEmptyComponent={
            !loading && !error ? (
              <AsyncStateView state="empty" title={t("tasks.emptyTitle")} message={t("tasks.emptyBody")} />
            ) : null
          }
          renderSectionHeader={({ section }) => (
            section.title ? (
              <View style={styles.sectionHeader}>
                <Text style={styles.sectionTitle}>{section.title}</Text>
                <View style={styles.countBadge}>
                  <Text style={styles.countBadgeText}>{section.data.length}</Text>
                </View>
              </View>
            ) : null
          )}
          renderItem={({ item }) => (
            <TaskBentoCard
              task={item}
              unread={unreadTaskIds.has(item.task_id)}
              isExecuting={runningTaskId === item.task_id}
              onPress={(task) => router.push(`/tasks/${task.task_id}`)}
              onExecute={(taskId) => void handleExecuteNow(taskId)}
              onTogglePause={(task) => void handleTogglePause(task)}
              onOpenExecution={(executionId) => router.push(`/task-executions/${executionId}`)}
            />
          )}
        />
      </View>
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

function taskStatusTone(task: Task): "normal" | "warning" | "danger" | "success" {
  if (task.work_status) {
    if (task.work_status.status === "waiting_for_you" || task.work_status.status === "paused") return "warning";
    if (task.work_status.status === "failed") return "danger";
    if (task.work_status.status === "completed") return "success";
    return "normal";
  }
  if (task.pending_approval_count > 0 || task.latest_execution_state === "waiting_approval") return "warning";
  if (task.latest_execution_state === "failed") return "danger";
  if (task.latest_execution_state === "completed") return "success";
  if (task.latest_execution_state === "paused" || task.state === "paused") return "warning";
  return "normal";
}

function launchLabel(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  if (task.launch_policy.kind === "scheduled") return t("tasks.launch.scheduled");
  if (task.launch_policy.kind === "event") return t("tasks.launch.event");
  return t("tasks.launch.manual");
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { flex: 1, backgroundColor: colors.background },
  updateBanner: {
    marginHorizontal: spacing.large,
    marginTop: spacing.small,
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  updateTitle: { color: colors.ink, fontWeight: "700" },
  updateDetail: { color: colors.muted, fontSize: 12, marginTop: spacing.xsmall },
  updateLink: { color: colors.accent, fontWeight: "700" },
  offlineBanner: {
    marginHorizontal: spacing.large,
    marginTop: spacing.small,
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.warningSoft,
    gap: spacing.xsmall,
  },
  offlineTitle: { color: colors.ink, fontWeight: "700" },
  offlineDetail: { color: colors.muted, fontSize: 12 },
  unreadNoticeBanner: {
    marginHorizontal: spacing.large,
    marginTop: spacing.small,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
    borderWidth: 1,
    borderColor: colors.line,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.small,
  },
  unreadNoticeLeft: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    minWidth: 0,
  },
  unreadNoticeTextWrap: {
    flex: 1,
    minWidth: 0,
  },
  unreadNoticeTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "700",
  },
  unreadNoticeDetail: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 1,
  },
  markAllReadButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.small,
    paddingVertical: 5,
    borderRadius: radii.small,
    borderWidth: 1,
    borderColor: colors.line,
  },
  markAllReadText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
  },
  filters: {
    flexDirection: "row",
    gap: spacing.small,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
  },
  filter: {
    paddingHorizontal: spacing.medium,
    paddingVertical: 6,
    borderRadius: radii.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  filterActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  filterText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
  },
  filterTextActive: {
    color: colors.onAccent,
    fontWeight: "700",
  },
  list: {
    paddingHorizontal: spacing.large,
    paddingBottom: 48,
    gap: spacing.medium,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    paddingTop: spacing.medium,
    paddingBottom: spacing.small,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 14,
    fontWeight: "800",
  },
  countBadge: {
    backgroundColor: colors.line,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radii.small,
  },
  countBadgeText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "700",
  },
  taskCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    flexDirection: "row",
    overflow: "hidden",
    ...shadows.card,
  },
  statusStripe: {
    width: 5,
    backgroundColor: colors.accent,
  },
  stripeWarning: { backgroundColor: colors.warning },
  stripeDanger: { backgroundColor: colors.danger },
  stripeSuccess: { backgroundColor: colors.accent },
  cardMain: {
    flex: 1,
    padding: spacing.large,
    gap: spacing.small,
  },
  taskHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.small,
  },
  titleRow: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  unreadDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.danger,
  },
  title: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "800",
    flex: 1,
  },
  stateBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radii.small,
    backgroundColor: colors.accentSoft,
  },
  badgeWarning: { backgroundColor: colors.warningSoft },
  badgeDanger: { backgroundColor: colors.dangerSoft },
  badgeSuccess: { backgroundColor: colors.accentSoft },
  stateText: { color: colors.accent, fontSize: 11, fontWeight: "700" },
  stateTextWarning: { color: colors.warning },
  stateTextDanger: { color: colors.danger },
  stateTextSuccess: { color: colors.accent },
  goal: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18,
  },
  failure: {
    color: colors.danger,
    fontSize: 12,
    lineHeight: 16,
  },
  latestResult: {
    padding: spacing.small,
    borderRadius: radii.small,
    backgroundColor: colors.background,
  },
  resultText: {
    color: colors.ink,
    fontSize: 12,
    lineHeight: 16,
  },
  cardFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: spacing.xsmall,
    paddingTop: spacing.small,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
  },
  metaGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  metaText: {
    color: colors.muted,
    fontSize: 11,
  },
  metaDivider: {
    color: colors.muted,
    fontSize: 11,
  },
  actionGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  quickButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.small,
    paddingVertical: 4,
    borderRadius: radii.small,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
  },
  quickButtonText: {
    color: colors.ink,
    fontSize: 11,
    fontWeight: "600",
  },
  executeButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.medium,
    paddingVertical: 5,
    borderRadius: radii.small,
    backgroundColor: colors.accent,
  },
  executeButtonText: {
    color: colors.onAccent,
    fontSize: 11,
    fontWeight: "700",
  },
});
