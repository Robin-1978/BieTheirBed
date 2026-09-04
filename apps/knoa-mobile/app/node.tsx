import { router, Stack, useLocalSearchParams } from "expo-router";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useState } from "react";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportLabelKey } from "@/api/transportPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { presentNodeName } from "@/presentation/nodePresentation";

export default function NodeMenuScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  const workspaceName = stringParam(params.workspaceName) || t("nav.workspace");
  const nodeId = stringParam(params.nodeId) || gateway.nodeId;
  const node = gateway.nodes.find((item) => item.nodeId === nodeId);
  const nodeName = presentNodeName(node, t("common.unnamedComputer"));
  const [advanced, setAdvanced] = useState(false);
  function returnToWorkspace() {
    if (workspaceId) router.replace({ pathname: "/workspaces/[workspaceId]", params: { workspaceId, workspaceName } });
    else router.replace("/account");
  }

  return (
    <>
      <Stack.Screen options={{ title: nodeName }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.nodeIcon}><AppIcon name="node" color={colors.accent} size={31} /></View>
          <Text style={styles.title}>{nodeName}</Text>
          <Text style={styles.meta}>{workspaceName}</Text>
          <View style={styles.statusRow}>
            <Text style={gateway.status === "ready" ? styles.online : styles.offline}>
              {gateway.status === "ready" ? t("nodeHeader.online") : t("nodeHeader.connecting")}
            </Text>
            {gateway.status === "ready" ? <Text style={styles.transport}>{t(transportLabelKey(gateway.transportMode))}</Text> : null}
          </View>
          {gateway.status === "ready" ? <Text style={styles.transportDetail}>{t("nodeSettings.autoTransportDetail")}</Text> : null}
        </View>

        <View style={styles.card}>
          <MenuRow icon="chat" title={t("header.chat")} detail={t("nodeMenu.chatDetail")} onPress={() => router.replace({ pathname: "/(tabs)", params: { workspaceId, workspaceName, nodeId } })} />
          <MenuRow icon="tasks" title={t("header.tasks")} detail={t("nodeMenu.tasksDetail")} onPress={() => router.replace({ pathname: "/(tabs)/tasks", params: { workspaceId, workspaceName, nodeId } })} />
          <MenuRow icon="file" title={t("nav.results")} detail={t("nodeMenu.resultsDetail")} onPress={() => router.push({ pathname: "/(tabs)/assets", params: { workspaceId, workspaceName, nodeId } })} />
          <MenuRow icon="agent" title={t("nav.nodeResources")} detail={t("nodeMenu.resourcesDetail")} onPress={() => router.push("/capabilities")} />
          <MenuRow icon="settings" title={t("nav.nodeSettings")} detail={t("nodeMenu.settingsDetail")} onPress={() => router.push("/settings/node")} />
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>{t("nodeMenu.connection")}</Text>
          <AppPressable onPress={() => void gateway.reconnect()} style={styles.secondary}>
            <Text style={styles.secondaryText}>{t("nodeMenu.reconnect")}</Text>
          </AppPressable>
          <AppPressable onPress={() => setAdvanced((value) => !value)} style={styles.advancedToggle}>
            <Text style={styles.secondaryText}>{advanced ? t("settings.collapseAdvanced") : t("settings.expandAdvanced")}</Text>
          </AppPressable>
          {advanced ? <View style={styles.advanced}>
            <Text style={styles.detail}>{t("common.nodeId")}</Text>
            <Text style={styles.mono} selectable>{nodeId || "—"}</Text>
            <Text style={styles.detail}>{t("common.gateway")}</Text>
            <Text style={styles.mono} selectable>{gateway.gatewayUrl || "—"}</Text>
          </View> : null}
        </View>

        <AppPressable onPress={returnToWorkspace} style={styles.leave}>
          <Text style={styles.leaveText}>{t("nodeMenu.backToWorkspace")}</Text>
        </AppPressable>
      </ScrollView>
    </>
  );
}

function MenuRow({ icon, title, detail, onPress }: { icon: AppIconName; title: string; detail: string; onPress(): void }) {
  return (
    <AppPressable onPress={onPress} style={styles.row}>
      <AppIcon name={icon} color={colors.accent} size={22} />
      <View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>
      <AppIcon name="chevron-right" color={colors.muted} size={18} />
    </AppPressable>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.large, paddingBottom: 48 },
  hero: { alignItems: "center", padding: spacing.xlarge, gap: spacing.small, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  nodeIcon: { width: 58, height: 58, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  title: { color: colors.ink, fontSize: 22, fontWeight: "800" },
  meta: { color: colors.muted, ...typography.small, marginTop: 2 },
  transport: { color: colors.ink, ...typography.small, fontWeight: "800" },
  transportDetail: { color: colors.muted, fontSize: 11, lineHeight: 16, textAlign: "center" },
  statusRow: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  online: { color: colors.accent, fontWeight: "800" },
  offline: { color: colors.muted, fontWeight: "700" },
  card: { paddingHorizontal: spacing.large, paddingVertical: spacing.xsmall, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  row: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: spacing.medium, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  flex: { flex: 1, minWidth: 0 },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  sectionTitle: { color: colors.ink, ...typography.subheading, fontWeight: "800", marginTop: spacing.medium },
  detail: { color: colors.muted, fontSize: 11, marginTop: spacing.small },
  mono: { color: colors.ink, fontFamily: "monospace", fontSize: 12 },
  secondary: { alignItems: "center", padding: spacing.medium, marginVertical: spacing.medium, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  advancedToggle: { alignItems: "center", paddingVertical: spacing.medium },
  advanced: { gap: spacing.xsmall, paddingTop: spacing.xsmall },
  leave: { alignItems: "center", padding: spacing.large },
  leaveText: { color: colors.accent, fontWeight: "800" },
});
