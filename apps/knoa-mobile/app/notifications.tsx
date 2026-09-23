import { router, Stack } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import {
  acknowledgeHubNotification,
  listHubNotifications,
  type HubNotificationIntent,
} from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { useConnection } from "@/state/GatewayProvider";
import { colors, radii, spacing, typography } from "@/theme";

type Category = HubNotificationIntent["category"];

const CATEGORY_ICON: Record<Category, string> = {
  completed: "check",
  failed: "x",
  cancelled: "x",
  approval_required: "alert",
  interaction_required: "chat",
  node_offline: "desktop",
  update_required: "refresh",
};

/**
 * Notification history. The banner + push flows only surface the latest
 * unread item and ack destructively; this page keeps every intent with
 * read state, so a dismissed notification can always be found again.
 */
export default function NotificationsScreen() {
  const { t } = useI18n();
  const { status } = useConnection();
  const [items, setItems] = useState<HubNotificationIntent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [ackingAll, setAckingAll] = useState(false);

  const load = useCallback(async () => {
    if (status !== "ready") {
      setLoading(false);
      return;
    }
    setLoadError(false);
    try {
      const result = await listHubNotifications(0);
      setItems(result.notifications);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  const categoryLabel = useCallback((category: Category) => {
    switch (category) {
      case "completed": return t("notifications.categoryCompleted");
      case "failed": return t("notifications.categoryFailed");
      case "cancelled": return t("notifications.categoryCancelled");
      case "approval_required": return t("notifications.categoryApproval");
      case "interaction_required": return t("notifications.categoryInteraction");
      case "node_offline": return t("notifications.categoryNodeOffline");
      case "update_required": return t("notifications.categoryUpdate");
    }
  }, [t]);

  async function open(item: HubNotificationIntent) {
    if (!item.acknowledged_at) {
      try {
        await acknowledgeHubNotification(item.intent_id);
      } catch {
        // Still navigate; the next reconcile marks it read server-side.
      }
      const now = Date.now() / 1000;
      setItems((current) => current.map((entry) => entry.intent_id === item.intent_id
        ? { ...entry, acknowledged_at: entry.acknowledged_at ?? now }
        : entry));
    }
    const link = item.deep_link;
    if (link.route === "task_execution" && link.execution_id) {
      router.push({ pathname: "/task-executions/[id]", params: { id: link.execution_id } });
    } else if (link.route === "task" && (link.task_id || item.work_id)) {
      router.push({ pathname: "/tasks/[id]", params: { id: link.task_id || item.work_id } });
    } else if (link.route === "conversation") {
      router.push("/(tabs)");
    } else if (link.route === "node") {
      router.push("/settings/node");
    } else if (link.route === "update") {
      router.push("/update");
    } else if (link.execution_id) {
      router.push({ pathname: "/task-executions/[id]", params: { id: link.execution_id } });
    } else if (link.task_id || item.work_id) {
      router.push({ pathname: "/tasks/[id]", params: { id: link.task_id || item.work_id } });
    }
  }

  async function markAllRead() {
    if (ackingAll) return;
    setAckingAll(true);
    try {
      const pending = items.filter((item) => !item.acknowledged_at);
      await Promise.allSettled(pending.map((item) => acknowledgeHubNotification(item.intent_id)));
      const now = Date.now() / 1000;
      setItems((current) => current.map((item) => ({ ...item, acknowledged_at: item.acknowledged_at ?? now })));
    } finally {
      setAckingAll(false);
    }
  }

  const unreadCount = items.filter((item) => !item.acknowledged_at).length;

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          title: t("notifications.title"),
          headerRight: () => (
            unreadCount > 0 ? (
              <AppPressable
                accessibilityRole="button"
                accessibilityLabel={t("reminders.markAllRead")}
                disabled={ackingAll}
                onPress={() => void markAllRead()}
                style={styles.headerAction}
              >
                {ackingAll
                  ? <ActivityIndicator size="small" color={colors.accent} />
                  : <Text style={styles.headerActionText}>{t("reminders.markAllRead")}</Text>}
              </AppPressable>
            ) : undefined
          ),
        }}
      />
      {loading ? <AsyncStateView state="loading" /> : null}
      {!loading && loadError && !items.length ? (
        <AsyncStateView state="error" retryLabel={t("tasks.reload")} onRetry={() => void load()} />
      ) : null}
      {!loading && !loadError && !items.length ? (
        <AsyncStateView state="empty" title={t("notifications.emptyTitle")} />
      ) : null}
      <FlatList
        data={items}
        keyExtractor={(item) => item.intent_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => {
          const read = Boolean(item.acknowledged_at);
          const expired = item.expires_at > 0 && item.expires_at < Date.now() / 1000;
          return (
            <AppPressable
              accessibilityRole="button"
              style={[styles.row, read && styles.rowRead, expired && styles.rowExpired]}
              onPress={() => void open(item)}
            >
              {!read ? <View style={styles.dot} /> : null}
              <AppIcon name={CATEGORY_ICON[item.category] as "check"} color={read ? colors.muted : colors.accent} size={18} />
              <View style={styles.body}>
                <Text style={[styles.category, read && styles.readText]}>{categoryLabel(item.category)}</Text>
                <Text style={[styles.title, read && styles.readText]} numberOfLines={2}>
                  {typeof item.parameters.title === "string" && item.parameters.title
                    ? item.parameters.title
                    : categoryLabel(item.category)}
                </Text>
                <Text style={styles.time}>{formatTime(item.received_at)}</Text>
              </View>
            </AppPressable>
          );
        }}
      />
    </View>
  );
}

function formatTime(value: number): string {
  const millis = value > 1e12 ? value : value * 1000;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  headerAction: { paddingHorizontal: spacing.small, paddingVertical: spacing.xsmall },
  headerActionText: { color: colors.accent, fontWeight: "700", fontSize: 14 },
  list: { padding: spacing.large, gap: spacing.small, paddingBottom: 48 },
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.small,
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.accentSoft,
    padding: spacing.medium,
  },
  rowRead: { borderColor: colors.line },
  rowExpired: { opacity: 0.6 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent, marginTop: 6 },
  body: { flex: 1, gap: 2 },
  category: { ...typography.tiny, color: colors.accent, fontWeight: "700" },
  title: { ...typography.subheading, color: colors.ink },
  readText: { color: colors.muted },
  time: { ...typography.tiny, color: colors.muted },
});
