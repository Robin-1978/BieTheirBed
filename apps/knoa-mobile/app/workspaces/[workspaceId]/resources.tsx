import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
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
import { colors } from "@/theme";

export default function WorkspaceResourcesScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const { t } = useI18n();
  const [state, setState] = useState<WorkspaceResourceState | null>(null);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [resources, directory] = await Promise.all([loadWorkspaceResourceState(), listHubNodes()]);
      setState(resources);
      setNodes(directory);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("resources.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));

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
      setError(caught instanceof Error ? caught.message : t("resources.connectHostFailed"));
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
      setError(caught instanceof Error ? caught.message : t("resources.connectUserNodeFailed"));
    } finally {
      setWorking("");
    }
  }

  async function revoke(grantId: string) {
    setWorking(grantId);
    setError("");
    try {
      await revokeWorkspaceResourceGrant(grantId);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("resources.revokeFailed"));
    } finally {
      setWorking("");
    }
  }

  function nodeName(nodeId: string) {
    return nodes.find((item) => item.node_id === nodeId)?.display_name || t("resources.unknownNode");
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
      {loading ? <ActivityIndicator color={colors.accent} /> : null}

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
                  <Text style={styles.rowTitle}>{node?.display_name || t("resources.unknownNode")}</Text>
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
                      onPress={() => void revoke(grant.grant_id)}
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

      {!loading && !resources.length ? (
        <View style={styles.empty}>
          <Text style={styles.cardTitle}>{t("resources.emptyTitle")}</Text>
          <Text style={styles.meta}>{t("resources.emptyDetail")}</Text>
          <AppPressable style={styles.primary} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params })}>
            <Text style={styles.primaryText}>{t("resources.selectNode")}</Text>
          </AppPressable>
        </View>
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  icon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  card: { padding: 15, gap: 12, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  row: { flexDirection: "row", alignItems: "center", gap: 11 },
  resourceIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  endpoint: { padding: 13, gap: 7, borderRadius: 13, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  healthy: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  warning: { color: colors.warning, fontSize: 12, fontWeight: "800" },
  secondary: { minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  linkButton: { minHeight: 34, alignItems: "flex-start", justifyContent: "center" },
  linkText: { color: colors.danger, fontSize: 12, fontWeight: "700" },
  empty: { padding: 18, gap: 11, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.accent },
  primaryText: { color: colors.white, fontWeight: "800" },
  error: { color: colors.danger, lineHeight: 20 },
});
