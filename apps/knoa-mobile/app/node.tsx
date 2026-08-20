import { router, Stack, useLocalSearchParams } from "expo-router";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportDetailKey, transportLabelKey } from "@/api/transportPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function NodeMenuScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  const workspaceName = stringParam(params.workspaceName) || t("nav.workspace");
  const nodeId = stringParam(params.nodeId) || gateway.nodeId;
  const node = gateway.nodes.find((item) => item.nodeId === nodeId);
  function returnToWorkspace() {
    if (workspaceId) router.replace({ pathname: "/workspaces/[workspaceId]", params: { workspaceId, workspaceName } });
    else router.replace("/account");
  }

  return (
    <>
      <Stack.Screen options={{ title: node?.displayName || t("nav.node") }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.nodeIcon}><AppIcon name="node" color={colors.accent} size={31} /></View>
          <Text style={styles.title}>{node?.displayName || nodeId || t("nav.node")}</Text>
          <Text style={styles.meta}>{workspaceName}</Text>
          <View style={styles.statusRow}>
            <Text style={gateway.status === "ready" ? styles.online : styles.offline}>
              {gateway.status === "ready" ? t("nodeHeader.online") : t("nodeHeader.connecting")}
            </Text>
            {gateway.status === "ready" ? <Text style={styles.transport}>{t(transportLabelKey(gateway.transportMode))}</Text> : null}
          </View>
          {gateway.status === "ready" ? <Text style={styles.transportDetail}>{t(transportDetailKey(gateway.transportMode))}</Text> : null}
        </View>

        <View style={styles.card}>
          <MenuRow icon="chat" title={t("header.chat")} detail={t("nodeMenu.chatDetail")} onPress={() => router.replace({ pathname: "/chat", params: { workspaceId, workspaceName, nodeId } })} />
          <MenuRow icon="tasks" title={t("header.tasks")} detail={t("nodeMenu.tasksDetail")} onPress={() => router.replace({ pathname: "/tasks", params: { workspaceId, workspaceName, nodeId } })} />
          <MenuRow icon="agent" title={t("nav.nodeResources")} detail={t("nodeMenu.resourcesDetail")} onPress={() => router.push("/capabilities")} />
          <MenuRow icon="settings" title={t("nav.nodeSettings")} detail={t("nodeMenu.settingsDetail")} onPress={() => router.push("/settings/node")} />
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>{t("nodeMenu.connection")}</Text>
          <Text style={styles.detail}>{t("common.nodeId")}</Text>
          <Text style={styles.mono} selectable>{nodeId || "—"}</Text>
          <Text style={styles.detail}>{t("common.gateway")}</Text>
          <Text style={styles.mono} selectable>{gateway.gatewayUrl || "—"}</Text>
          <AppPressable onPress={() => void gateway.reconnect()} style={styles.secondary}>
            <Text style={styles.secondaryText}>{t("nodeMenu.reconnect")}</Text>
          </AppPressable>
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
  container: { padding: 17, gap: 14, paddingBottom: 48 },
  hero: { alignItems: "center", padding: 20, gap: 7, borderRadius: 19, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  nodeIcon: { width: 58, height: 58, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  title: { color: colors.ink, fontSize: 22, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, marginTop: 2 },
  transport: { color: colors.ink, fontSize: 12, fontWeight: "800" },
  transportDetail: { color: colors.muted, fontSize: 11, lineHeight: 16, textAlign: "center" },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  online: { color: colors.accent, fontWeight: "800" },
  offline: { color: colors.muted, fontWeight: "700" },
  card: { paddingHorizontal: 15, paddingVertical: 4, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  row: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  flex: { flex: 1, minWidth: 0 },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800", marginTop: 10 },
  detail: { color: colors.muted, fontSize: 11, marginTop: 7 },
  mono: { color: colors.ink, fontFamily: "monospace", fontSize: 12 },
  secondary: { alignItems: "center", padding: 13, marginVertical: 12, borderRadius: 13, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  leave: { alignItems: "center", padding: 14 },
  leaveText: { color: colors.accent, fontWeight: "800" },
});
