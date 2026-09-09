import * as Clipboard from "expo-clipboard";
import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, Share, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { WorkspaceCacheBanner } from "@/components/WorkspaceCacheBanner";
import {
  createNodeEnrollmentCode,
  listHubNodes,
  loadWorkspaceResourceState,
  type HubNode,
  type NodeEnrollmentCode,
  type WorkspaceDeployment,
} from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { updateNodeDirectGatewayUrl } from "@/security/deviceIdentity";
import { loadWorkspaceCache, mergeWorkspaceCache, type WorkspaceCacheSnapshot } from "@/storage/workspaceCache";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { userFacingError } from "@/ui/userFacingError";
import { presentHubNodeName } from "@/presentation/nodePresentation";

export default function WorkspaceNodesScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const { t, locale } = useI18n();
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [deployments, setDeployments] = useState<WorkspaceDeployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [cacheSnapshot, setCacheSnapshot] = useState<WorkspaceCacheSnapshot | null>(null);
  const [working, setWorking] = useState("");
  const [enrollmentCode, setEnrollmentCode] = useState("");
  const [enrollmentPayload, setEnrollmentPayload] = useState<NodeEnrollmentCode | null>(null);
  const [enrollmentExpiresAt, setEnrollmentExpiresAt] = useState(0);
  const [showRawPayload, setShowRawPayload] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const applyCache = useCallback((snapshot: WorkspaceCacheSnapshot) => {
    setCacheSnapshot(snapshot);
    setNodes(snapshot.nodes);
    setDeployments(snapshot.resources?.workspaceDeployments ?? []);
  }, []);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setRefreshing(true);
    setError("");
    try {
      const [directory, resources] = await Promise.all([
        listHubNodes(),
        loadWorkspaceResourceState(),
      ]);
      setNodes(directory);
      setDeployments(resources.workspaceDeployments);
      await mergeWorkspaceCache(params.workspaceId, { nodes: directory, resources });
    } catch (caught) {
      setError(userFacingError(caught, t("nodes.loadFailed")));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [params.workspaceId, t]);

  useFocusEffect(useCallback(() => {
    let active = true;
    void (async () => {
      const cached = await loadWorkspaceCache(params.workspaceId);
      if (active && cached) { applyCache(cached); setLoading(false); }
      await refresh(!cached);
    })();
    return () => { active = false; };
  }, [applyCache, params.workspaceId, refresh]));

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
      void gateway.switchNode(node.node_id).catch(() => undefined);
      router.push({ pathname: "/(tabs)", params: { ...params, nodeId: node.node_id } });
    } catch (caught) {
      setError(userFacingError(caught, t("nodes.connectFailed")));
    } finally {
      setWorking("");
    }
  }

  async function generateEnrollmentCode() {
    setWorking("enrollment");
    setError("");
    setCopied(false);
    try {
      const payload = await createNodeEnrollmentCode();
      setEnrollmentPayload(payload);
      setEnrollmentCode(JSON.stringify(payload));
      setEnrollmentExpiresAt(payload.expires_at);
    } catch (caught) {
      setError(userFacingError(caught, t("nodes.enrollmentFailed")));
    } finally {
      setWorking("");
    }
  }

  async function copyEnrollmentCode() {
    if (!enrollmentCode) return;
    await Clipboard.setStringAsync(enrollmentCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
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
        <WorkspaceCacheBanner snapshot={cacheSnapshot} loading={refreshing} error={error} onRefresh={() => void refresh()} />

        {loading && !nodes.length ? <AsyncStateView state="loading" /> : null}
        {error && !loading && !nodes.length ? (
          <AsyncStateView state="error" message={error} retryLabel={t("common.refresh")} onRetry={() => void refresh()} />
        ) : null}

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
                  <Text style={styles.nodeName}>{presentHubNodeName(node, t("common.unnamedComputer"))}</Text>
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
                  ? <ActivityIndicator color={colors.onAccent} size="small" />
                  : <Text style={styles.enterText}>{bound ? t("nodes.enterNode") : t("nodes.pairApp")}</Text>}
              </AppPressable>
            </View>
          );
        })}

        {enrollmentPayload ? (
          <View style={styles.card}>
            <View style={styles.enrollmentHeader}>
              <View style={styles.enrollmentTitleRow}>
                <AppIcon name="node" color={colors.accent} size={18} />
                <Text style={styles.nodeName}>{t("nodes.addNode")}</Text>
              </View>
              <View style={styles.grantBadge}>
                <Text style={styles.grantBadgeText}>{t("nodes.grantId")}: {enrollmentPayload.grant_id.slice(0, 8)}</Text>
              </View>
            </View>
            <Text style={styles.meta}>{t("nodes.enrollmentHint")}</Text>

            <View style={styles.tokenBox}>
              <Text style={styles.tokenLabel}>Hub</Text>
              <Text numberOfLines={1} style={styles.tokenValue}>{enrollmentPayload.hub_url}</Text>
            </View>

            <Text style={styles.meta}>
              {t("nodes.codeExpires", {
                time: new Date(enrollmentExpiresAt * 1000).toLocaleTimeString(locale === "en-US" ? "en-US" : "zh-CN"),
              })}
            </Text>

            <View style={styles.enrollmentActions}>
              <AppPressable style={styles.copyButton} onPress={() => void copyEnrollmentCode()}>
                <AppIcon name={copied ? "check" : "file"} color={colors.onAccent} size={16} />
                <Text style={styles.copyButtonText}>{copied ? t("nodes.copied") : t("nodes.copyCode")}</Text>
              </AppPressable>
              <AppPressable style={styles.secondary} onPress={() => void shareEnrollmentCode()}>
                <Text style={styles.secondaryText}>{t("nodes.shareCode")}</Text>
              </AppPressable>
            </View>

            <AppPressable onPress={() => setShowRawPayload((v) => !v)} style={styles.rawToggle}>
              <Text style={styles.rawToggleText}>{showRawPayload ? t("nodes.hideRaw") : t("nodes.viewRaw")}</Text>
              <AppIcon name={showRawPayload ? "chevron-up" : "chevron-down"} color={colors.muted} size={14} />
            </AppPressable>

            {showRawPayload ? (
              <Text selectable style={styles.code}>{JSON.stringify(enrollmentPayload, null, 2)}</Text>
            ) : null}

            <Text style={styles.meta}>{t("nodes.afterEnrollmentHint")}</Text>
          </View>
        ) : null}

        {!loading && !error && nodes.length === 0 && !enrollmentCode ? (
          <AsyncStateView state="empty" title={t("nodes.noNodesYet")} message={t("nodes.emptyDetail")} />
        ) : null}

        {error && nodes.length > 0 ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 48 },
  header: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  icon: { width: 48, height: 48, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, ...typography.heading },
  meta: { color: colors.muted, ...typography.small, lineHeight: 18 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center", borderRadius: radii.medium },
  callout: { padding: spacing.large, gap: spacing.small, borderRadius: radii.large, backgroundColor: colors.accentSoft, borderWidth: 1, borderColor: colors.accent },
  calloutTitle: { color: colors.ink, ...typography.title },
  card: { padding: spacing.large, gap: spacing.medium, borderRadius: radii.large, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, ...shadows.card },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  nodeName: { color: colors.ink, fontSize: 16, fontWeight: "700" },
  online: { color: colors.accent, fontWeight: "700", fontSize: 12 },
  offline: { color: colors.muted, fontWeight: "700", fontSize: 12 },
  enter: { minHeight: 42, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent },
  enterText: { color: colors.onAccent, fontWeight: "700" },
  secondary: { flex: 1, minHeight: 42, flexDirection: "row", gap: spacing.small, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  enrollmentHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  enrollmentTitleRow: { flexDirection: "row", alignItems: "center", gap: spacing.small },
  grantBadge: { paddingHorizontal: spacing.small, paddingVertical: 2, borderRadius: radii.small, backgroundColor: colors.accentFaint },
  grantBadgeText: { color: colors.accent, ...typography.tiny, fontWeight: "700" },
  tokenBox: { padding: spacing.medium, borderRadius: radii.small, backgroundColor: colors.surfaceMuted, gap: 2 },
  tokenLabel: { color: colors.muted, ...typography.tiny },
  tokenValue: { color: colors.ink, ...typography.small, fontFamily: "monospace" },
  enrollmentActions: { flexDirection: "row", gap: spacing.medium },
  copyButton: { flex: 1, minHeight: 42, flexDirection: "row", gap: spacing.small, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent },
  copyButtonText: { color: colors.onAccent, fontWeight: "700" },
  rawToggle: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4, paddingVertical: spacing.xsmall },
  rawToggleText: { color: colors.muted, ...typography.tiny },
  code: { color: colors.ink, fontFamily: "monospace", fontSize: 11, lineHeight: 16, padding: spacing.medium, borderRadius: radii.small, backgroundColor: colors.background },
  error: { color: colors.danger, lineHeight: 20 },
});
