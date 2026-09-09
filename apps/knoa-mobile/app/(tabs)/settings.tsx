import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportLabelKey } from "@/api/transportPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { presentNodeName } from "@/presentation/nodePresentation";

export default function NodeAndSettingsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  const workspaceName = stringParam(params.workspaceName) || t("nav.workspace");
  const nodeId = stringParam(params.nodeId) || gateway.nodeId;
  const node = gateway.nodes.find((item) => item.nodeId === nodeId);
  const nodeName = presentNodeName(node, t("common.unnamedComputer"));

  const [working, setWorking] = useState("");

  const reconnect = useCallback(async () => {
    setWorking("reconnect");
    try {
      await gateway.reconnect();
    } finally {
      setWorking("");
    }
  }, [gateway]);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.container}>
      {/* Agent Persona & Node Hub Hero */}
      <View style={styles.heroCard}>
        <View style={styles.heroHeader}>
          <View style={styles.nodeIconWrap}>
            <AppIcon name="agent" color={colors.accent} size={28} />
          </View>
          <View style={styles.flex}>
            <Text style={styles.nodeTitle}>小诺</Text>
            <Text style={styles.workspaceText}>
              {nodeName} · {workspaceName}
            </Text>
          </View>
          <View style={styles.statusBadge}>
            <Text style={gateway.status === "ready" ? styles.onlineText : styles.offlineText}>
              {gateway.status === "ready" ? t("nodeHeader.online") : t("nodeHeader.connecting")}
            </Text>
          </View>
        </View>

        {gateway.status === "ready" ? (
          <View style={styles.transportRow}>
            <Text style={styles.transportLabel}>{t("nodeSettings.activeTransport")}:</Text>
            <Text style={styles.transportValue}>{t(transportLabelKey(gateway.transportMode))}</Text>
          </View>
        ) : null}

        <View style={styles.quickActions}>
          <AppPressable
            disabled={Boolean(working)}
            onPress={() => void reconnect()}
            style={styles.quickActionButton}
          >
            <AppIcon name="refresh" color={colors.accent} size={16} />
            <Text style={styles.quickActionText}>
              {working === "reconnect" ? t("nodeHeader.connecting") : t("nodeMenu.reconnect")}
            </Text>
          </AppPressable>
          <AppPressable
            onPress={() => router.push({ pathname: "/settings/node", params: { workspaceId, workspaceName, nodeId } })}
            style={styles.quickActionButton}
          >
            <AppIcon name="pulse" color={colors.accent} size={16} />
            <Text style={styles.quickActionText}>{t("nav.nodeSettings")}</Text>
          </AppPressable>
        </View>
      </View>

      {/* 1. Agent Mind & Capabilities */}
      <View style={styles.sectionCard}>
        <Text style={styles.sectionTitle}>{t("settings.agentPersonaTitle")}</Text>
        <SettingRow
          icon="history"
          title={t("settings.memoriesTitle")}
          detail={t("settings.memoriesDetail")}
          onPress={() => router.push("/memories")}
        />
        <SettingRow
          icon="code"
          title={t("nav.extensions")}
          detail={t("settings.extensions.addHint")}
          onPress={() => router.push("/settings/extensions")}
        />
        <SettingRow
          icon="desktop"
          title={t("nav.models")}
          detail={t("settings.models.heroDetail")}
          onPress={() => router.push("/settings/models")}
        />
        <SettingRow
          icon="sparkles"
          title={t("nav.agents")}
          detail={t("settings.agents.heroDetail")}
          onPress={() => router.push("/settings/agents")}
        />
        <SettingRow
          icon="node"
          title={t("nav.nodeResources")}
          detail={t("nodeMenu.resourcesDetail")}
          onPress={() => router.push("/capabilities")}
        />
      </View>

      {/* 2. Host Computer & Workspaces */}
      <View style={styles.sectionCard}>
        <Text style={styles.sectionTitle}>{t("settings.hostAndNetworkTitle")}</Text>
        <SettingRow
          icon="workspace"
          title={t("nodeSwitch.workspaceHub")}
          detail={workspaceName}
          onPress={() => {
            if (workspaceId) router.push({ pathname: "/workspaces/[workspaceId]", params: { workspaceId, workspaceName } });
            else router.push("/account");
          }}
        />
        <SettingRow
          icon="user"
          title={t("nav.account")}
          detail={t("account.appSection")}
          onPress={() => router.push("/account")}
        />
        <SettingRow
          icon="pulse"
          title={t("nav.nodeSettings")}
          detail={nodeName}
          onPress={() => router.push({ pathname: "/settings/node", params: { workspaceId, workspaceName, nodeId } })}
        />
      </View>

      {/* 3. Preferences & System */}
      <View style={styles.sectionCard}>
        <Text style={styles.sectionTitle}>{t("settings.systemAndPrefsTitle")}</Text>
        <SettingRow
          icon="settings"
          title={t("nav.appSettings")}
          detail={t("settings.appearanceHint")}
          onPress={() => router.push("/settings/app")}
        />
        <SettingRow
          icon="refresh"
          title={t("nav.update")}
          detail={t("settings.checkAppUpdateHint")}
          onPress={() => router.push("/update")}
        />
        <SettingRow
          icon="settings"
          title={t("settings.systemConfiguration")}
          detail={t("settings.systemConfigurationDetail")}
          onPress={() => router.push("/settings/system")}
        />
      </View>
    </ScrollView>
  );
}

function SettingRow({
  icon,
  title,
  detail,
  onPress,
}: {
  icon: string;
  title: string;
  detail: string;
  onPress(): void;
}) {
  const iconName: AppIconName = icon === "sparkles" ? "agent" : (icon as AppIconName);
  return (
    <AppPressable onPress={onPress} style={styles.row}>
      <AppIcon name={iconName} color={colors.accent} size={20} />
      <View style={styles.flex}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.rowDetail} numberOfLines={1}>{detail}</Text>
      </View>
      <AppIcon name="chevron-right" color={colors.muted} size={16} />
    </AppPressable>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  container: {
    padding: spacing.large,
    gap: spacing.large,
    paddingBottom: 48,
  },
  heroCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.large,
    gap: spacing.medium,
    ...shadows.card,
  },
  heroHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
  },
  nodeIconWrap: {
    width: 48,
    height: 48,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  flex: {
    flex: 1,
    minWidth: 0,
  },
  nodeTitle: {
    color: colors.ink,
    fontSize: 20,
    fontWeight: "700",
  },
  workspaceText: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radii.small,
  },
  onlineText: {
    color: colors.accent,
    fontWeight: "700",
    fontSize: 12,
  },
  offlineText: {
    color: colors.muted,
    fontWeight: "700",
    fontSize: 12,
  },
  transportRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    paddingTop: spacing.xsmall,
  },
  transportLabel: {
    color: colors.muted,
    fontSize: 12,
  },
  transportValue: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "700",
  },
  quickActions: {
    flexDirection: "row",
    gap: spacing.medium,
    marginTop: spacing.xsmall,
  },
  quickActionButton: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
  },
  quickActionText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  sectionCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.xsmall,
    ...shadows.card,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "700",
    marginTop: spacing.medium,
    marginBottom: spacing.small,
  },
  row: {
    minHeight: 56,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  rowTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "700",
  },
  rowDetail: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 1,
  },
});
