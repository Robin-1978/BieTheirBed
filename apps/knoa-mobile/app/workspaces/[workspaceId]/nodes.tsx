import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, Share, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  createNodeEnrollmentCode,
  listHubNodes,
  loadWorkspaceResourceState,
  type HubNode,
  type WorkspaceDeployment,
} from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { updateNodeDirectGatewayUrl } from "@/security/deviceIdentity";
import { colors } from "@/theme";

export default function WorkspaceNodesScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const { t, locale } = useI18n();
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [deployments, setDeployments] = useState<WorkspaceDeployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [enrollmentCode, setEnrollmentCode] = useState("");
  const [enrollmentExpiresAt, setEnrollmentExpiresAt] = useState(0);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [directory, resources] = await Promise.all([
        listHubNodes(),
        loadWorkspaceResourceState(),
      ]);
      setNodes(directory);
      setDeployments(resources.workspaceDeployments);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("nodes.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));

  const unboundOnlineNodes = nodes.filter(
    (node) => node.online && !gateway.nodes.some((item) => item.nodeId === node.node_id),
  );

  async function enter(node: HubNode) {
    const bound = gateway.nodes.some((item) => item.nodeId === node.node_id);
    if (!node.online) {
      setError(bound ? t("nodes.offlineEnterBlocked") : t("nodes.offlinePairBlocked"));
      return;
    }
    if (!bound) {
      router.push({ pathname: "/pair", params });
      return;
    }
    setWorking(node.node_id);
    setError("");
    try {
      await updateNodeDirectGatewayUrl(node.node_id, node.direct_gateway_url || "");
      router.push({ pathname: "/node", params: { ...params, nodeId: node.node_id } });
      void gateway.switchNode(node.node_id).catch(() => undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("nodes.connectFailed"));
    } finally {
      setWorking("");
    }
  }

  async function generateEnrollmentCode() {
    setWorking("enrollment");
    setError("");
    try {
      const payload = await createNodeEnrollmentCode();
      setEnrollmentCode(JSON.stringify(payload));
      setEnrollmentExpiresAt(payload.expires_at);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("nodes.enrollmentFailed"));
    } finally {
      setWorking("");
    }
  }

  async function shareEnrollmentCode() {
    if (!enrollmentCode) return;
    await Share.share({ message: enrollmentCode, title: t("nodes.enrollmentShareTitle") });
  }

  return (
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.header}>
          <View style={styles.icon}>
            <AppIcon name="node" color={colors.accent} size={27} />
          </View>
          <View style={styles.flex}>
            <Text style={styles.title}>{t("nodes.title")}</Text>
            <Text style={styles.meta}>{t("nodes.headerDetail")}</Text>
          </View>
          <AppPressable
            accessibilityLabel={t("nodes.addNode")}
            disabled={Boolean(working)}
            onPress={() => void generateEnrollmentCode()}
            style={styles.iconButton}
          >
            {working === "enrollment"
              ? <ActivityIndicator color={colors.accent} size="small" />
              : <AppIcon name="plus" color={colors.accent} size={21} />}
          </AppPressable>
          <AppPressable accessibilityLabel={t("common.refresh")} onPress={() => void refresh()} style={styles.iconButton}>
            <AppIcon name="refresh" color={colors.muted} size={20} />
          </AppPressable>
        </View>

        {loading ? <ActivityIndicator color={colors.accent} /> : null}

        {unboundOnlineNodes.length > 0 ? (
          <View style={styles.callout}>
            <Text style={styles.calloutTitle}>{t("nodes.pairingReadyTitle")}</Text>
            <Text style={styles.meta}>{t("nodes.pairingReadyDetail")}</Text>
          </View>
        ) : null}

        {nodes.map((node) => {
          const bound = gateway.nodes.some((item) => item.nodeId === node.node_id);
          const count = deployments.filter((item) => item.target_node_id === node.node_id).length;
          return (
            <View key={node.node_id} style={styles.card}>
              <View style={styles.row}>
                <AppIcon name="node" color={node.online ? colors.accent : colors.muted} size={24} />
                <View style={styles.flex}>
                  <Text style={styles.nodeName}>{node.display_name}</Text>
                  <Text style={styles.meta}>
                    {node.platform} {node.version} · {count} {t("nodes.deployments")} · {bound ? t("nodes.appPaired") : t("nodes.appUnpaired")}
                  </Text>
                </View>
                <Text style={node.online ? styles.online : styles.offline}>
                  {node.online ? t("nodes.online") : t("nodes.offline")}
                </Text>
              </View>
              {!node.online ? <Text style={styles.meta}>{t("nodes.offlineHint")}</Text> : null}
              <AppPressable disabled={Boolean(working)} onPress={() => void enter(node)} style={styles.enter}>
                {working === node.node_id
                  ? <ActivityIndicator color={colors.white} size="small" />
                  : <Text style={styles.enterText}>{bound ? t("nodes.enterNode") : t("nodes.pairApp")}</Text>}
              </AppPressable>
            </View>
          );
        })}

        {enrollmentCode ? (
          <View style={styles.card}>
            <Text style={styles.nodeName}>{t("nodes.addNode")}</Text>
            <Text style={styles.meta}>{t("nodes.enrollmentHint")}</Text>
            <Text selectable style={styles.code}>{enrollmentCode}</Text>
            <Text style={styles.meta}>
              {t("nodes.codeExpires", {
                time: new Date(enrollmentExpiresAt * 1000).toLocaleTimeString(locale === "en-US" ? "en-US" : "zh-CN"),
              })}
            </Text>
            <AppPressable style={styles.secondary} onPress={() => void shareEnrollmentCode()}>
              <Text style={styles.secondaryText}>{t("nodes.shareCode")}</Text>
            </AppPressable>
            <Text style={styles.meta}>{t("nodes.afterEnrollmentHint")}</Text>
          </View>
        ) : null}

        {!loading && nodes.length === 0 && !enrollmentCode ? (
          <View style={styles.emptyState}>
            <Text style={styles.nodeName}>{t("nodes.noNodesYet")}</Text>
            <Text style={styles.meta}>{t("nodes.emptyDetail")}</Text>
          </View>
        ) : null}

        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 12, paddingBottom: 48 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  icon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center", borderRadius: 13 },
  callout: { padding: 14, gap: 6, borderRadius: 16, backgroundColor: colors.accentSoft, borderWidth: 1, borderColor: colors.accent },
  calloutTitle: { color: colors.ink, fontWeight: "800" },
  card: { padding: 15, gap: 12, borderRadius: 17, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  row: { flexDirection: "row", alignItems: "center", gap: 11 },
  nodeName: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  online: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  offline: { color: colors.muted, fontWeight: "700", fontSize: 12 },
  enter: { minHeight: 42, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent },
  enterText: { color: colors.white, fontWeight: "800" },
  secondary: { minHeight: 42, borderRadius: 12, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  code: { color: colors.ink, fontFamily: "monospace", fontSize: 11, lineHeight: 16, padding: 10, borderRadius: 10, backgroundColor: colors.background },
  emptyState: { padding: 18, gap: 5, borderRadius: 17, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  error: { color: colors.danger, lineHeight: 20 },
});
