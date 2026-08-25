import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { WorkspaceCacheBanner } from "@/components/WorkspaceCacheBanner";
import {
  listHubNodes,
  loadWorkspaceResourceState,
  revokeWorkspaceResourceGrant,
  type HubNode,
  type WorkspaceResourceState,
} from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { updateNodeDirectGatewayUrl } from "@/security/deviceIdentity";
import { useGateway } from "@/state/GatewayProvider";
import { loadWorkspaceCache, mergeWorkspaceCache, type WorkspaceCacheSnapshot } from "@/storage/workspaceCache";
import { colors, radii, shadows, spacing, typography } from "@/theme";
import { presentHubNodeName } from "@/presentation/nodePresentation";
import { userFacingError } from "@/ui/userFacingError";

export default function WorkspaceResourcesScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const { t } = useI18n();
  const [state, setState] = useState<WorkspaceResourceState | null>(null);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [cacheSnapshot, setCacheSnapshot] = useState<WorkspaceCacheSnapshot | null>(null);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  const applyCache = useCallback((snapshot: WorkspaceCacheSnapshot) => {
    setCacheSnapshot(snapshot);
    setState(snapshot.resources);
    setNodes(snapshot.nodes);
  }, []);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setRefreshing(true);
    setError("");
    try {
      const [resources, directory] = await Promise.all([loadWorkspaceResourceState(), listHubNodes()]);
      setState(resources);
      setNodes(directory);
      await mergeWorkspaceCache(params.workspaceId, { nodes: directory, resources });
    } catch (caught) {
      setError(userFacingError(caught, t("resources.loadFailed")));
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

  async function manageOnNode(nodeId: string, kind: "model" | "mcp") {
    const node = nodes.find((item) => item.node_id === nodeId);
    if (!node?.online) {
      setError(t("resources.hostOffline"));
      return;
    }
    if (!gateway.nodes.some((item) => item.nodeId === nodeId)) {
      setError(t("resources.pairHostFirst"));
      return;
    }
    setWorking(nodeId);
    setError("");
    try {
      await updateNodeDirectGatewayUrl(nodeId, node.direct_gateway_url || "");
      await gateway.switchNode(nodeId);
      router.push(kind === "model" ? "/settings/models" : "/settings/extensions");
    } catch (caught) {
      setError(userFacingError(caught, t("resources.connectHostFailed")));
    } finally {
      setWorking("");
    }
  }

  async function useModelOnNode(nodeId: string) {
    const node = nodes.find((item) => item.node_id === nodeId);
    if (!node?.online) {
      setError(t("resources.userNodeOffline"));
      return;
    }
    if (!gateway.nodes.some((item) => item.nodeId === nodeId)) {
      setError(t("resources.pairUserNodeFirst"));
      return;
    }
    setWorking(nodeId);
    setError("");
    try {
      await updateNodeDirectGatewayUrl(nodeId, node.direct_gateway_url || "");
      await gateway.switchNode(nodeId);
      router.push("/settings/models");
    } catch (caught) {
      setError(userFacingError(caught, t("resources.connectUserNodeFailed")));
    } finally {
      setWorking("");
    }
  }

  function confirmRevoke(grantId: string, nodeId: string) {
    Alert.alert(
      t("resources.revokeGrantTitle"),
      t("resources.revokeGrantMessage", { node: nodeName(nodeId) }),
      [
        { text: t("common.cancel"), style: "cancel" },
        { text: t("resources.revokeGrantConfirm"), style: "destructive", onPress: () => void revoke(grantId) },
      ],
    );
  }

  async function revoke(grantId: string) {
    setWorking(grantId);
    setError("");
    try {
      await revokeWorkspaceResourceGrant(grantId);
      await refresh();
    } catch (caught) {
      setError(userFacingError(caught, t("resources.revokeFailed")));
    } finally {
      setWorking("");
    }
  }

  function nodeName(nodeId: string) {
    return presentHubNodeName(nodes.find((item) => item.node_id === nodeId), t("common.unnamedComputer"));
  }

  function healthLabel(node: HubNode | undefined, observation: WorkspaceResourceState["observations"][number] | undefined) {
    if (!node?.online) return t("nodes.offline");
    if (observation?.health === "healthy") {
      return t("resources.healthyCapacity", { capacity: observation.available_capacity });
    }
    return t("resources.onlineWaitingHealth");
  }

  const resources = state?.workspaceResources.filter((resource) => resource.enabled) ?? [];

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.header}>
        <View style={styles.icon}><AppIcon name="share" color={colors.accent} size={27} /></View>
        <View style={styles.flex}>
          <Text style={styles.title}>{t("resources.pageTitle")}</Text>
          <Text style={styles.meta}>{t("resources.headerDetail")}</Text>
        </View>
        <AppPressable accessibilityLabel={t("common.refresh")} onPress={() => void refresh()} style={styles.iconButton}>
          <AppIcon name="refresh" color={colors.muted} size={20} />
        </AppPressable>
      </View>
      <WorkspaceCacheBanner snapshot={cacheSnapshot} loading={refreshing} error={error} onRefresh={() => void refresh()} />
      {loading && !resources.length ? <AsyncStateView state="loading" /> : null}
      {error && !loading && !resources.length ? (
        <AsyncStateView state="error" message={error} retryLabel={t("common.refresh")} onRetry={() => void refresh()} />
      ) : null}

      {resources.map((resource) => {
        const deployments = state?.workspaceDeployments.filter((item) => item.resource_id === resource.resource_id && item.enabled) ?? [];
        return (
          <View key={resource.resource_id} style={styles.card}>
            <View style={styles.row}>
              <View style={styles.resourceIcon}>
                <AppIcon name={resource.kind === "model" ? "agent" : "share"} color={colors.accent} size={23} />
              </View>
              <View style={styles.flex}>
                <Text style={styles.cardTitle}>{resource.display_name}</Text>
                <Text style={styles.meta}>{resource.kind === "model" ? t("resources.sharedModel") : t("resources.sharedMcp")}</Text>
              </View>
            </View>
            {deployments.map((deployment) => {
              const node = nodes.find((item) => item.node_id === deployment.target_node_id);
              const observation = state?.observations.find((item) => item.deployment_id === deployment.deployment_id);
              const grants = state?.grants.filter((grant) => grant.target_deployment_id === deployment.deployment_id && grant.revoked_at === null) ?? [];
              const grantNames = grants.map((grant) => nodeName(grant.caller_node_id)).join("、");
              return (
                <View key={deployment.deployment_id} style={styles.endpoint}>
                  <Text style={styles.rowTitle}>{presentHubNodeName(node, t("common.unnamedComputer"))}</Text>
                  <Text style={observation?.health === "healthy" && node?.online ? styles.healthy : styles.warning}>
                    {healthLabel(node, observation)}
                  </Text>
                  <Text style={styles.meta}>
                    {grants.length
                      ? t("resources.grantsAllowed", { nodes: grantNames || t("resources.oneNode") })
                      : t("resources.noGrants")}
                  </Text>
                  {resource.kind === "model" ? grants.map((grant) => (
                    <AppPressable
                      key={`use:${grant.grant_id}`}
                      disabled={Boolean(working)}
                      style={styles.secondary}
                      onPress={() => void useModelOnNode(grant.caller_node_id)}
                    >
                      {working === grant.caller_node_id
                        ? <ActivityIndicator color={colors.accent} />
                        : <Text style={styles.secondaryText}>{t("resources.useOnNode", { node: nodeName(grant.caller_node_id) })}</Text>}
                    </AppPressable>
                  )) : null}
                  {grants.map((grant) => (
                    <AppPressable
                      key={grant.grant_id}
                      disabled={Boolean(working)}
                      style={styles.linkButton}
                      onPress={() => confirmRevoke(grant.grant_id, grant.caller_node_id)}
                    >
                      <Text style={styles.linkText}>
                        {working === grant.grant_id
                          ? t("resources.revokingGrant")
                          : t("resources.revokeGrant", { node: nodeName(grant.caller_node_id) })}
                      </Text>
                    </AppPressable>
                  ))}
                  <AppPressable
                    disabled={Boolean(working)}
                    style={styles.secondary}
                    onPress={() => void manageOnNode(deployment.target_node_id, resource.kind)}
                  >
                    {working === deployment.target_node_id
                      ? <ActivityIndicator color={colors.accent} />
                      : <Text style={styles.secondaryText}>{t("resources.manageOnHostNode")}</Text>}
                  </AppPressable>
                </View>
              );
            })}
          </View>
        );
      })}

      {!loading && !error && !resources.length ? (
        <>
          <AsyncStateView state="empty" title={t("resources.emptyTitle")} message={t("resources.emptyDetail")} />
          <AppPressable style={styles.primary} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params })}>
            <Text style={styles.primaryText}>{t("resources.selectNode")}</Text>
          </AppPressable>
        </>
      ) : null}
      {error && resources.length > 0 ? <Text style={styles.error}>{error}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 52 },
  header: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  icon: { width: 48, height: 48, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, ...typography.small, lineHeight: 18 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  card: { padding: spacing.medium, gap: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  resourceIcon: { width: 44, height: 44, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  endpoint: { padding: spacing.medium, gap: spacing.small, borderRadius: radii.medium, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  healthy: { color: colors.accent, ...typography.small, fontWeight: "800" },
  warning: { color: colors.warning, ...typography.small, fontWeight: "800" },
  secondary: { minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  linkButton: { minHeight: 34, alignItems: "flex-start", justifyContent: "center" },
  linkText: { color: colors.danger, ...typography.small, fontWeight: "700" },
  primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accent },
  primaryText: { color: colors.onAccent, fontWeight: "800" },
  error: { color: colors.danger, lineHeight: 20 },
});
