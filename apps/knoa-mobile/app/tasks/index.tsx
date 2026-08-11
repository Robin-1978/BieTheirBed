import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { Task, TaskDefinitionState } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { PrimarySwipeNavigation } from "@/components/PrimarySwipeNavigation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type Filter = "current" | TaskDefinitionState;

export default function TasksScreen() {
  const gateway = useGateway();
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
    } catch {
      setError(t("tasks.loadFailed"));
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, t]);

  useEffect(() => { void refresh(); }, [refresh, gateway.latestEvent]);

  const visibleTasks = useMemo(
    () => tasks.filter((task) => filter === "current" ? task.state !== "archived" : task.state === filter),
    [filter, tasks],
  );

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
      {loading ? <ActivityIndicator color={colors.accent} style={styles.loader} /> : null}
      {error ? (
        <View style={styles.errorCard}>
          <Text style={styles.errorText}>{error}</Text>
          <AppPressable onPress={() => void refresh()}><Text style={styles.retry}>{t("tasks.reload")}</Text></AppPressable>
        </View>
      ) : null}
      <FlatList
        data={visibleTasks}
        keyExtractor={(task) => task.task_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh()} />}
        contentContainerStyle={styles.list}
        ListEmptyComponent={!loading && !error ? (
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{t("tasks.emptyTitle")}</Text>
            <Text style={styles.emptyText}>{t("tasks.emptyBody")}</Text>
          </View>
        ) : null}
        renderItem={({ item }) => (
          <AppPressable
            accessibilityRole="button"
            accessibilityLabel={`${item.title}，${stateLabel(item.state, t)}，${t("tasks.executions", { count: item.execution_count })}`}
            style={styles.task}
            onPress={() => router.push(`/tasks/${item.task_id}`)}
          >
            <View style={styles.taskHeader}>
              <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
              <Text style={[styles.state, item.state === "paused" && styles.paused]}>{stateLabel(item.state, t)}</Text>
            </View>
            <Text style={styles.goal} numberOfLines={3}>{item.goal}</Text>
            <View style={styles.metaRow}>
              <View style={styles.metaCopy}>
                <Text style={styles.meta}>{launchLabel(item, t)}</Text>
                <Text style={styles.meta}>{t("tasks.executions", { count: item.execution_count })}</Text>
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
  filters: { flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  filter: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 14, backgroundColor: colors.surface },
  filterActive: { backgroundColor: colors.accentSoft },
  filterText: { color: colors.muted },
  filterTextActive: { color: colors.accent, fontWeight: "600" },
  loader: { marginTop: 32 },
  errorCard: { margin: 16, padding: 16, borderRadius: 14, backgroundColor: colors.dangerSoft, gap: 8 },
  errorText: { color: colors.danger },
  retry: { color: colors.accent, fontWeight: "700" },
  list: { padding: 16, gap: 12, flexGrow: 1 },
  task: { padding: 16, backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, gap: 8 },
  taskHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  title: { flex: 1, color: colors.ink, fontWeight: "700", fontSize: 17 },
  state: { color: colors.accent, fontWeight: "600", fontSize: 12 },
  paused: { color: colors.warning },
  goal: { color: colors.ink, fontSize: 15, lineHeight: 22 },
  metaRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  metaCopy: { flexDirection: "row", justifyContent: "space-between", flex: 1, marginRight: 8 },
  meta: { color: colors.muted, fontSize: 12 },
  empty: { alignItems: "center", paddingTop: 64, gap: 8 },
  emptyTitle: { color: colors.ink, fontWeight: "700", fontSize: 17 },
  emptyText: { color: colors.muted, textAlign: "center" },
});
