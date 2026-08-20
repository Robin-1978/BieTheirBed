import { router, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";
import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { navigatePrimary, type PrimaryScreen } from "@/components/PrimarySwipeNavigation";
import { useI18n } from "@/i18n";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { rememberNodePage } from "@/navigation/navigationPreference";
import { colors } from "@/theme";

export function HeaderActions({ current }: { current: PrimaryScreen }) {
  const { t } = useI18n();
  const { unreadCount } = useTaskReminders();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const nodeParams = {
    workspaceId: stringParam(params.workspaceId),
    workspaceName: stringParam(params.workspaceName),
    nodeId: stringParam(params.nodeId),
  };
  useEffect(() => {
    if (!nodeParams.workspaceId || !nodeParams.nodeId) return;
    void rememberNodePage({ ...nodeParams, nodePage: current });
  }, [current, nodeParams.nodeId, nodeParams.workspaceId, nodeParams.workspaceName]);
  return (
      <View style={styles.container}>
        <HeaderTab
          icon="chat"
          label={t("header.chat")}
          selected={current === "chat"}
          onPress={() => navigatePrimary(current, "chat", nodeParams)}
        />
        <HeaderTab
          icon="tasks"
          label={t("header.tasks")}
          selected={current === "tasks"}
          badge={unreadCount}
          onPress={() => navigatePrimary(current, "tasks", nodeParams)}
        />
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={t("header.nodeMenu")}
          hitSlop={8}
          onPress={() => router.push({ pathname: "/node", params: nodeParams })}
          style={styles.action}
        >
          <AppIcon name="more" color={colors.muted} size={22} />
        </AppPressable>
      </View>
  );
}

function HeaderTab({
  icon,
  label,
  selected,
  badge = 0,
  onPress,
}: {
  icon: "chat" | "tasks";
  label: string;
  selected: boolean;
  badge?: number;
  onPress(): void;
}) {
  return (
    <AppPressable
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      accessibilityLabel={label}
      disabled={selected}
      hitSlop={8}
      onPress={onPress}
      style={styles.action}
    >
      <View style={[styles.tabIcon, selected && styles.selectedTabIcon]}>
        <AppIcon name={icon} color={selected ? colors.accent : colors.muted} size={21} />
        {badge > 0 ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{badge > 9 ? "9+" : badge}</Text>
          </View>
        ) : null}
      </View>
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginRight: 2,
  },
  action: {
    width: 44,
    height: 44,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: 13,
  },
  tabIcon: { width: 38, height: 36, alignItems: "center", justifyContent: "center", borderRadius: 11 },
  selectedTabIcon: { backgroundColor: colors.accentSoft },
  badge: { position: "absolute", right: 1, top: 0, minWidth: 16, height: 16, paddingHorizontal: 3, borderRadius: 8, backgroundColor: colors.danger, alignItems: "center", justifyContent: "center" },
  badgeText: { color: "white", fontSize: 9, fontWeight: "800" },
});

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}
