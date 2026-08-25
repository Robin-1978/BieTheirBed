import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import type { Task } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { shareResultJson, shareResultPdf, shareResultText } from "@/api/shareResult";
import { loadTaskCache, storeTaskCache } from "@/storage/taskCache";
import { presentNodeName } from "@/presentation/nodePresentation";
import { resultOutcome } from "@/components/resultSummaryPresentation";

export default function ResultsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [sharing, setSharing] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [timeFilter, setTimeFilter] = useState<"all" | "7d" | "30d">("all");
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const [nodeFilter, setNodeFilter] = useState(params.nodeId?.trim() || gateway.nodeId || "");
  const taskCacheScope = nodeFilter || gateway.nodeId || "unselected";

  const refresh = useCallback(async (manual = false) => {
    if (!gateway.client) {
      setLoading(false);
      setError(gateway.status === "error" ? t("results.loadFailed") : t("chat.reconnecting"));
      setRefreshing(false);
      return;
    }
    if (manual) setRefreshing(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.listTasks({ includeArchived: true, limit: 100 }));
      setTasks(result.tasks);
      void storeTaskCache(taskCacheScope, result.tasks);
    } catch {
      setError(t("results.loadFailed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [gateway.client, gateway.runAuthenticated, gateway.status, t, taskCacheScope]);

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

  const results = useMemo(
    () => tasks
      .filter((task) => Boolean(task.latest_execution_id || task.latest_execution_summary || task.latest_execution_failure_code))
      .filter((task) => !agentFilter || task.agent_id === agentFilter)
      .filter((task) => timeFilter === "all" || (task.latest_execution_updated_at ?? task.updated_at) >= Date.now() / 1000 - (timeFilter === "7d" ? 7 : 30) * 86400)
      .sort((left, right) => (right.latest_execution_updated_at ?? right.updated_at) - (left.latest_execution_updated_at ?? left.updated_at)),
    [agentFilter, tasks, timeFilter],
  );

  return (
    <ScrollView contentContainerStyle={styles.container} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh(true)} />}>
      <View style={styles.hero}>
        <View style={styles.icon}><AppIcon name="file" color={colors.accent} size={26} /></View>
        <View style={styles.flex}><Text style={styles.title}>{t("results.title")}</Text><Text style={styles.meta}>{t("results.detail")}</Text></View>
      </View>
      <AppPressable style={styles.artifactAction} onPress={() => router.push({ pathname: "/artifacts", params: { sessionHandle: gateway.sessionHandle } })}><Text style={styles.artifactActionText}>{t("artifacts.title")}</Text></AppPressable>
      <View style={styles.filterSection}>
        <Text style={styles.filterLabel}>{t("results.filterAgent")}</Text>
        <View style={styles.filters}>
          <AppPressable style={[styles.filter, !agentFilter && styles.filterActive]} onPress={() => setAgentFilter("")}><Text style={[styles.filterText, !agentFilter && styles.filterTextActive]}>{t("results.allAgents")}</Text></AppPressable>
          {gateway.agents.map((agent) => <AppPressable key={agent.agent_id} style={[styles.filter, agentFilter === agent.agent_id && styles.filterActive]} onPress={() => setAgentFilter(agent.agent_id)}><Text style={[styles.filterText, agentFilter === agent.agent_id && styles.filterTextActive]}>{agent.display_name}</Text></AppPressable>)}
        </View>
        <Text style={styles.filterLabel}>{t("results.filterNode")}</Text>
        <View style={styles.filters}>
          {gateway.nodes.map((node) => <AppPressable key={node.nodeId} style={[styles.filter, nodeFilter === node.nodeId && styles.filterActive]} onPress={() => setNodeFilter(node.nodeId)}><Text style={[styles.filterText, nodeFilter === node.nodeId && styles.filterTextActive]}>{node.displayName}</Text></AppPressable>)}
        </View>
        <Text style={styles.filterLabel}>{t("results.filterTime")}</Text>
        <View style={styles.filters}>
          {(["all", "7d", "30d"] as const).map((value) => <AppPressable key={value} style={[styles.filter, timeFilter === value && styles.filterActive]} onPress={() => setTimeFilter(value)}><Text style={[styles.filterText, timeFilter === value && styles.filterTextActive]}>{t(`results.time.${value}` as never)}</Text></AppPressable>)}
        </View>
        <Text style={styles.nodeScope}>{t("results.nodeScope", { node: presentNodeName(gateway.nodes.find((item) => item.nodeId === nodeFilter), t("common.unnamedComputer")) })}</Text>
      </View>
      {loading ? <AsyncStateView state="loading" /> : null}
      {error ? <AsyncStateView state="error" message={error} retryLabel={t("results.retry")} onRetry={() => void refresh(true)} /> : null}
      {!loading && !error && !results.length ? <AsyncStateView state="empty" title={t("results.emptyTitle")} message={t("results.emptyDetail")} /> : null}
      {results.map((task) => {
        const outcome = resultOutcome(task);
        return (
        <View key={task.task_id} style={styles.card}>
          <View style={styles.cardHeader}><View style={styles.flex}><Text style={styles.cardTitle} numberOfLines={2}>{task.title}</Text><Text style={styles.meta}>{resultState(task, t)}</Text></View><AppIcon name={outcome.incomplete ? "alert" : "check"} color={outcome.incomplete ? colors.danger : colors.accent} size={20} /></View>
          {task.latest_execution_summary ? <Text style={styles.result} numberOfLines={5}>{task.latest_execution_summary}</Text> : null}
          {outcome.incomplete ? <Text style={styles.failure}>{t("results.failure", { code: outcome.failureCode || "unknown" })}</Text> : null}
          <View style={styles.facts}>
            <Text style={styles.fact}>{t("results.summary.evidence")}{outcome.evidenceExecutionId ? t("results.summary.execution") : t("results.summary.noExecution")}</Text>
            <Text style={[styles.fact, styles.factNext]}>{t("results.summary.next")}{t(`results.next.${outcome.nextStep}` as never)}</Text>
          </View>
          <View style={styles.actions}>
            {task.latest_execution_id ? <AppPressable style={styles.primaryAction} onPress={() => router.push(`/task-executions/${task.latest_execution_id}`)}><Text style={styles.primaryText}>{t("results.openExecution")}</Text></AppPressable> : null}
            <AppPressable style={styles.secondaryAction} onPress={() => router.push({ pathname: `/tasks/${task.task_id}`, params })}><Text style={styles.secondaryText}>{t("results.openTask")}</Text></AppPressable>
            {task.session_handle ? <AppPressable style={styles.secondaryAction} onPress={() => router.push({ pathname: "/artifacts", params: { sessionHandle: task.session_handle } })}><Text style={styles.secondaryText}>{t("results.openArtifacts")}</Text></AppPressable> : null}
            {task.latest_execution_summary ? <>
              <AppPressable style={styles.secondaryAction} disabled={sharing === task.task_id} onPress={async () => { setSharing(task.task_id); try { await shareResultText(task.title, `# ${task.title}\n\n${task.latest_execution_summary}`); } catch { setError(t("results.shareFailed")); } finally { setSharing(""); } }}><Text style={styles.secondaryText}>{sharing === task.task_id ? t("results.sharing") : t("results.share")}</Text></AppPressable>
              <AppPressable style={styles.secondaryAction} disabled={sharing === task.task_id} onPress={async () => { setSharing(task.task_id); try { await shareResultJson(task.title, task); } catch { setError(t("results.shareFailed")); } finally { setSharing(""); } }}><Text style={styles.secondaryText}>{t("results.shareJson")}</Text></AppPressable>
              <AppPressable style={styles.secondaryAction} disabled={sharing === task.task_id} onPress={async () => { setSharing(task.task_id); try { await shareResultPdf(task.title, `${task.title}\n\n${task.latest_execution_summary}`); } catch { setError(t("results.shareFailed")); } finally { setSharing(""); } }}><Text style={styles.secondaryText}>{t("results.sharePdf")}</Text></AppPressable>
            </> : null}
          </View>
        </View>
        );
      })}
    </ScrollView>
  );
}

function resultState(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  if (task.work_status) {
    return ({
      queued: t("taskState.queued"),
      working: t("taskState.running"),
      waiting_for_you: t("taskState.waitingApproval"),
      completed: t("taskState.completed"),
      failed: t("taskState.failed"),
      paused: t("tasks.state.paused"),
      cancelled: t("taskState.cancelled"),
    } as const)[task.work_status.status];
  }
  if (task.latest_execution_state === "completed") return t("results.completed");
  if (task.latest_execution_state === "failed") return t("results.failed");
  if (task.latest_execution_state === "running") return t("results.running");
  if (task.latest_execution_state === "waiting_approval") return t("results.waitingApproval");
  if (task.latest_execution_state === "queued") return t("taskState.queued");
  if (task.latest_execution_state === "paused") return t("tasks.state.paused");
  if (task.latest_execution_state === "cancelled") return t("taskState.cancelled");
  return t("results.available");
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 52 }, hero: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card }, icon: { width: 48, height: 48, alignItems: "center", justifyContent: "center", borderRadius: radii.large, backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, ...typography.heading }, meta: { color: colors.muted, ...typography.small, lineHeight: 18 }, artifactAction: { minHeight: 42, justifyContent: "center", alignItems: "center", borderRadius: radii.medium, borderWidth: 1, borderColor: colors.accent }, artifactActionText: { color: colors.accent, fontWeight: "800" }, filterSection: { padding: spacing.medium, gap: spacing.small, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card }, filterLabel: { color: colors.muted, ...typography.small, fontWeight: "700" }, filters: { flexDirection: "row", gap: spacing.small, flexWrap: "wrap" }, filter: { minHeight: 34, paddingHorizontal: spacing.medium, justifyContent: "center", borderRadius: radii.large, borderWidth: 1, borderColor: colors.line }, filterActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft }, filterText: { color: colors.muted, ...typography.small, fontWeight: "700" }, filterTextActive: { color: colors.accent }, nodeScope: { color: colors.muted, fontSize: 11 }, card: { padding: spacing.large, gap: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card }, cardHeader: { flexDirection: "row", alignItems: "flex-start", gap: spacing.medium }, cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" }, result: { color: colors.ink, lineHeight: 21 }, failure: { color: colors.danger, fontSize: 12 }, facts: { gap: spacing.xsmall, padding: spacing.medium, borderRadius: radii.medium, backgroundColor: colors.accentFaint }, fact: { color: colors.muted, fontSize: 11, lineHeight: 16 }, factNext: { color: colors.accent, fontWeight: "700" }, actions: { flexDirection: "row", flexWrap: "wrap", gap: spacing.small }, primaryAction: { minHeight: 40, justifyContent: "center", paddingHorizontal: spacing.medium, borderRadius: radii.medium, backgroundColor: colors.accent }, primaryText: { color: colors.onAccent, fontWeight: "800" }, secondaryAction: { minHeight: 40, justifyContent: "center", paddingHorizontal: spacing.medium, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.accent }, secondaryText: { color: colors.accent, fontWeight: "800" },
});
