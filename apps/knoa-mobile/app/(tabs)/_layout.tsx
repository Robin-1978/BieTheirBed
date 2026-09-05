import { Tabs, router, useLocalSearchParams } from "expo-router";
import { StyleSheet, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { NodeHeaderTitle } from "@/components/NodeHeader";
import { useI18n } from "@/i18n";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function TabsLayout() {
  const { t } = useI18n();
  const gateway = useGateway();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const { unreadCountForNode } = useTaskReminders();

  const currentNodeId = gateway.nodeId || stringParam(params.nodeId);
  const currentUnreadCount = unreadCountForNode(currentNodeId);

  const nodeParams = {
    workspaceId: stringParam(params.workspaceId),
    workspaceName: stringParam(params.workspaceName),
    nodeId: currentNodeId,
  };

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: colors.background },
        headerTintColor: colors.ink,
        headerTitle: () => <NodeHeaderTitle />,
        tabBarStyle: styles.tabBar,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        tabBarLabelStyle: styles.tabLabel,
        sceneStyle: { backgroundColor: colors.background },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: t("tabs.chat"),
          tabBarLabel: t("tabs.chat"),
          tabBarIcon: ({ color, size }) => <AppIcon name="chat" color={color} size={size ?? 22} />,
          headerRight: () => (
            <View style={styles.headerRightGroup}>
              <AppPressable
                accessibilityLabel={t("nav.conversations")}
                onPress={() => router.push({ pathname: "/conversations", params: nodeParams })}
                style={styles.headerButton}
              >
                <AppIcon name="history" color={colors.ink} size={21} />
              </AppPressable>
              <AppPressable
                accessibilityLabel={t("nav.nodeSettings")}
                onPress={() => router.push({ pathname: "/settings/node", params: nodeParams })}
                style={styles.headerButton}
              >
                <AppIcon name="settings" color={colors.ink} size={21} />
              </AppPressable>
            </View>
          ),
        }}
      />
      <Tabs.Screen
        name="tasks"
        options={{
          title: t("tabs.tasks"),
          tabBarLabel: t("tabs.tasks"),
          tabBarBadge: currentUnreadCount > 0 ? (currentUnreadCount > 9 ? "9+" : currentUnreadCount) : undefined,
          tabBarBadgeStyle: styles.badge,
          tabBarIcon: ({ color, size }) => <AppIcon name="tasks" color={color} size={size ?? 22} />,
          headerRight: () => (
            <View style={styles.headerRightGroup}>
              <AppPressable
                accessibilityLabel={t("nav.newTask")}
                onPress={() => router.push({ pathname: "/tasks/new", params: nodeParams })}
                style={styles.headerButton}
              >
                <AppIcon name="plus" color={colors.accent} size={23} />
              </AppPressable>
            </View>
          ),
        }}
      />
      <Tabs.Screen
        name="assets"
        options={{
          title: t("tabs.assets"),
          tabBarLabel: t("tabs.assets"),
          tabBarIcon: ({ color, size }) => <AppIcon name="file" color={color} size={size ?? 22} />,
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: t("tabs.settings"),
          tabBarLabel: t("tabs.settings"),
          tabBarIcon: ({ color, size }) => <AppIcon name="settings" color={color} size={size ?? 22} />,
        }}
      />
    </Tabs>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: colors.surface,
    borderTopColor: colors.line,
    borderTopWidth: StyleSheet.hairlineWidth,
    minHeight: 56,
    paddingBottom: 6,
    paddingTop: 6,
  },
  tabLabel: {
    fontSize: 11,
    fontWeight: "700",
  },
  badge: {
    backgroundColor: colors.danger,
    fontSize: 9,
    fontWeight: "800",
  },
  headerRightGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginRight: 10,
  },
  headerButton: {
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 19,
  },
});
