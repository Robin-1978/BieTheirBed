import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import type { Task } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function ResultsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();

  const refresh = useCallback(async (manual = false) => {
    if (!gateway.client) return;
    if (manual) setRefreshing(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.listTasks({ includeArchived: true, limit: 100 }));
      setTasks(result.tasks);
    } catch {
      setError(t("results.loadFailed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [gateway.client, gateway.runAuthenticated, t]);

  useEffect(() => { void refresh(); }, [refresh]);

  const results = useMemo(
    () => tasks
      .filter((task) => Boolean(task.latest_execution_id || task.latest_execution_summary || task.latest_execution_failure_code))
      .sort((left, right) => (right.latest_execution_updated_at ?? right.updated_at) - (left.latest_execution_updated_at ?? left.updated_at)),
    [tasks],
  );

  return (
    <ScrollView contentContainerStyle={styles.container} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh(true)} />}>
      <View style={styles.hero}>
        <View style={styles.icon}><AppIcon name="file" color={colors.accent} size={26} /></View>
        <View style={styles.flex}><Text style={styles.title}>{t("results.title")}</Text><Text style={styles.meta}>{t("results.detail")}</Text></View>
      </View>
      {loading ? <ActivityIndicator color={colors.accent} /> : null}
      {error ? <View style={styles.errorCard}><Text style={styles.error}>{error}</Text><AppPressable onPress={() => void refresh(true)}><Text style={styles.link}>{t("results.retry")}</Text></AppPressable></View> : null}
      {!loading && !error && !results.length ? <View style={styles.empty}><Text style={styles.emptyTitle}>{t("results.emptyTitle")}</Text><Text style={styles.meta}>{t("results.emptyDetail")}</Text></View> : null}
      {results.map((task) => (
        <View key={task.task_id} style={styles.card}>
          <View style={styles.cardHeader}><View style={styles.flex}><Text style={styles.cardTitle} numberOfLines={2}>{task.title}</Text><Text style={styles.meta}>{resultState(task, t)}</Text></View><AppIcon name={task.latest_execution_state === "failed" ? "alert" : "check"} color={task.latest_execution_state === "failed" ? colors.danger : colors.accent} size={20} /></View>
          {task.latest_execution_summary ? <Text style={styles.result} numberOfLines={5}>{task.latest_execution_summary}</Text> : null}
          {task.latest_execution_failure_code ? <Text style={styles.failure}>{t("results.failure", { code: task.latest_execution_failure_code })}</Text> : null}
          <View style={styles.actions}>
            {task.latest_execution_id ? <AppPressable style={styles.primaryAction} onPress={() => router.push(`/task-executions/${task.latest_execution_id}`)}><Text style={styles.primaryText}>{t("results.openExecution")}</Text></AppPressable> : null}
            <AppPressable style={styles.secondaryAction} onPress={() => router.push({ pathname: `/tasks/${task.task_id}`, params })}><Text style={styles.secondaryText}>{t("results.openTask")}</Text></AppPressable>
          </View>
        </View>
      ))}
    </ScrollView>
  );
}

function resultState(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  if (task.latest_execution_state === "completed") return t("results.completed");
  if (task.latest_execution_state === "failed") return t("results.failed");
  if (task.latest_execution_state === "running") return t("results.running");
  if (task.latest_execution_state === "waiting_approval") return t("results.waitingApproval");
  return t("results.available");
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 }, hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, icon: { width: 48, height: 48, alignItems: "center", justifyContent: "center", borderRadius: 15, backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 20, fontWeight: "800" }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, card: { padding: 15, gap: 10, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, cardHeader: { flexDirection: "row", alignItems: "flex-start", gap: 10 }, cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" }, result: { color: colors.ink, lineHeight: 21 }, failure: { color: colors.danger, fontSize: 12 }, actions: { flexDirection: "row", flexWrap: "wrap", gap: 8 }, primaryAction: { minHeight: 40, justifyContent: "center", paddingHorizontal: 13, borderRadius: 11, backgroundColor: colors.accent }, primaryText: { color: colors.white, fontWeight: "800" }, secondaryAction: { minHeight: 40, justifyContent: "center", paddingHorizontal: 13, borderRadius: 11, borderWidth: 1, borderColor: colors.accent }, secondaryText: { color: colors.accent, fontWeight: "800" }, empty: { padding: 22, alignItems: "center", gap: 6, borderRadius: 17, backgroundColor: colors.surface }, emptyTitle: { color: colors.ink, fontWeight: "800" }, errorCard: { padding: 14, borderRadius: 14, backgroundColor: colors.dangerSoft, gap: 7 }, error: { color: colors.danger }, link: { color: colors.accent, fontWeight: "800" },
});
