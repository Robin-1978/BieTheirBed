import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Animated, Linking, ScrollView, Share, StyleSheet, Text, View } from "react-native";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { WorkspaceCacheBanner } from "@/components/WorkspaceCacheBanner";
import {
  createNodeEnrollmentCode,
  listHostedWorkspaceMembers,
  listHostedWorkspaces,
  listHubNodes,
  listWorkspaceWork,
  loadHubConnection,
  loadWorkspaceResourceState,
  selectHostedWorkspace,
  type HostedWorkspace,
  type HubNode,
} from "@/hub/hubClient";
import { rememberWorkspace } from "@/navigation/navigationPreference";
import { triggerWelcomeHealthCheck } from "@/onboarding/firstConnect";
import { useGateway } from "@/state/GatewayProvider";
import { loadWorkspaceCache, mergeWorkspaceCache, type WorkspaceCacheSnapshot } from "@/storage/workspaceCache";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { userFacingError } from "@/ui/userFacingError";
import { presentHubNodeName, presentNodeName } from "@/presentation/nodePresentation";

const HOSTED_HUB_URL = "https://knoa.tinydotdot.com";

export default function WorkspaceScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string; connected?: string }>();
  const workspaceId = value(params.workspaceId);
  const { t } = useI18n();
  const fallbackName = value(params.workspaceName) || t("nav.workspace");
  const gateway = useGateway();

  const [workspace, setWorkspace] = useState<HostedWorkspace | null>(null);
  const [counts, setCounts] = useState({ work: 0, resources: 0, nodes: 0, onlineNodes: 0, members: 0 });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [cacheSnapshot, setCacheSnapshot] = useState<WorkspaceCacheSnapshot | null>(null);
  const [error, setError] = useState("");
  const [connectWorking, setConnectWorking] = useState(false);
  const [connectMessage, setConnectMessage] = useState("");
  const [showConnected, setShowConnected] = useState(value(params.connected) === "1");
  const [downloadUrl, setDownloadUrl] = useState(`${HOSTED_HUB_URL}/download`);
  const [switchingNodeId, setSwitchingNodeId] = useState("");
  const celebration = useRef(new Animated.Value(0)).current;
  const healthCheckStarted = useRef(false);

  const applyCache = useCallback((snapshot: WorkspaceCacheSnapshot) => {
    setCacheSnapshot(snapshot);
    setWorkspace(snapshot.workspace);
    setCounts({
      work: snapshot.work.length,
      resources: snapshot.resources?.workspaceResources.filter((item) => item.enabled).length ?? 0,
      nodes: snapshot.nodes.length,
      onlineNodes: snapshot.nodes.filter((node) => node.online).length,
      members: snapshot.members.length,
    });
  }, []);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setRefreshing(true);
    setError("");
    try {
      const connection = await loadHubConnection();
      if (!connection) { router.replace("/account/login"); return; }
      const hosted = connection.accountId ? await listHostedWorkspaces() : [];
      const target = hosted.find((item) => item.workspaceId === workspaceId) ?? {
        workspaceId: connection.workspaceId,
        displayName: fallbackName,
        kind: "personal" as const,
        role: "owner" as const,
        workspacePath: "",
      };
      if (connection.accountId && target.workspaceId !== connection.workspaceId) {
        await gateway.disconnectNode();
        await selectHostedWorkspace(target);
      }
      const [nodes, resources, work, members] = await Promise.all([
        listHubNodes(),
        loadWorkspaceResourceState().catch(() => null),
        listWorkspaceWork().catch(() => []),
        connection.accountId ? listHostedWorkspaceMembers(target.workspaceId).catch(() => []) : Promise.resolve([]),
      ]);
      const snapshot: WorkspaceCacheSnapshot = {
        version: 1,
        workspaceId,
        updatedAt: Date.now(),
        workspace: target,
        nodes,
        resources,
        work,
        members,
      };
      applyCache(snapshot);
      await rememberWorkspace(target.workspaceId, target.displayName);
      await mergeWorkspaceCache(workspaceId, snapshot);
    } catch (caught) { setError(userFacingError(caught, t("workspace.loadFailed"))); }
    finally { setLoading(false); setRefreshing(false); }
  }, [applyCache, fallbackName, gateway.disconnectNode, t, workspaceId]);

  useFocusEffect(useCallback(() => {
    let active = true;
    void (async () => {
      const cached = await loadWorkspaceCache(workspaceId);
      if (active && cached) {
        applyCache(cached);
        setLoading(false);
      }
      await refresh(!cached);
    })();
    return () => { active = false; };
  }, [applyCache, refresh, workspaceId]));

  useEffect(() => {
    let active = true;
    void loadHubConnection().then((connection) => {
      if (!active || !connection?.rootUrl) return;
      setDownloadUrl(`${connection.rootUrl.replace(/\/$/, "")}/download`);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (value(params.connected) !== "1") return;
    setShowConnected(true);
    router.setParams({ connected: "" });
  }, [params.connected]);

  useEffect(() => {
    if (!showConnected) return;
    celebration.setValue(0);
    Animated.spring(celebration, {
      toValue: 1,
      useNativeDriver: true,
      damping: 14,
      stiffness: 180,
    }).start();
  }, [celebration, showConnected]);

  useEffect(() => {
    if (!showConnected || healthCheckStarted.current || gateway.status !== "ready") return;
    healthCheckStarted.current = true;
    void triggerWelcomeHealthCheck(gateway, workspaceId, {
      title: t("taskTemplates.healthTitle"),
      goal: t("taskTemplates.healthGoal"),
    });
  }, [gateway, gateway.status, showConnected, t, workspaceId]);

  async function startConnectFlow() {
    if (connectWorking) return;
    setConnectWorking(true);
    setConnectMessage("");
    setError("");
    try {
      const payload = await createNodeEnrollmentCode();
      await Share.share({
        message: JSON.stringify(payload),
        title: t("nodes.enrollmentShareTitle"),
      });
      setConnectMessage(t("workspace.onboardingCodeShared"));
      router.push({
        pathname: "/pair",
        params: {
          ...routeParams,
          autoScan: "1",
          onboarding: "1",
        },
      });
    } catch (caught) {
      setError(userFacingError(caught, t("nodes.enrollmentFailed")));
    } finally {
      setConnectWorking(false);
    }
  }

  const displayName = workspace?.displayName || fallbackName;
  const routeParams = { workspaceId, workspaceName: displayName };

  const currentNodeBinding = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  const activeNodeName = currentNodeBinding
    ? presentNodeName(currentNodeBinding, t("common.unnamedComputer"))
    : (cacheSnapshot?.nodes.find((n) => n.node_id === gateway.nodeId)?.display_name || t("common.unnamedComputer"));

  const handleLaunchActiveNode = () => {
    const targetNodeId = gateway.nodeId || cacheSnapshot?.nodes[0]?.node_id;
    router.push({
      pathname: "/(tabs)",
      params: {
        workspaceId,
        workspaceName: displayName,
        nodeId: targetNodeId,
      },
    });
  };

  const handleSwitchAndLaunch = async (node: HubNode) => {
    setSwitchingNodeId(node.node_id);
    try {
      await gateway.switchNode(node.node_id);
    } catch {
      // Continue to navigation even if switch fails initially
    } finally {
      setSwitchingNodeId("");
    }
    router.push({
      pathname: "/(tabs)",
      params: {
        workspaceId,
        workspaceName: displayName,
        nodeId: node.node_id,
      },
    });
  };

  return (
    <>
      <Stack.Screen options={{ title: displayName }} />
      <ScrollView contentContainerStyle={styles.container}>
        {/* Workspace Hero Header */}
        <View style={styles.hero}>
          <View style={styles.heroIcon}>
            <AppIcon name="workspace" color={colors.accent} size={28} />
          </View>
          <View style={styles.flex}>
            <Text style={styles.title} numberOfLines={1}>{displayName}</Text>
            <Text style={styles.meta}>
              {workspace?.kind === "shared" ? t("workspace.shared") : t("workspace.personal")} · {roleLabel(workspace?.role, t)}
            </Text>
          </View>
          <AppPressable
            accessibilityLabel={t("workspace.accountHome")}
            onPress={() => router.push("/account")}
            style={styles.iconButton}
          >
            <AppIcon name="user" color={colors.muted} size={24} />
          </AppPressable>
        </View>

        <WorkspaceCacheBanner
          snapshot={cacheSnapshot}
          loading={refreshing}
          error={error}
          onRefresh={() => void refresh()}
        />

        {/* Primary Action Card: Launch Agent to Connected Node */}
        {gateway.nodeId || counts.nodes > 0 ? (
          <AppPressable
            style={styles.activeNodeCard}
            onPress={handleLaunchActiveNode}
          >
            <View style={styles.activeNodeIcon}>
              <AppIcon name="node" color={colors.onAccent} size={26} />
            </View>
            <View style={styles.flex}>
              <View style={styles.activeNodeHeaderRow}>
                <Text style={styles.activeNodeLabel}>{t("workspace.activeNode")}</Text>
                <View style={styles.activeStatusPill}>
                  <View style={[styles.statusDot, gateway.status === "ready" ? styles.statusDotOnline : styles.statusDotOffline]} />
                  <Text style={styles.activeStatusText}>
                    {gateway.status === "ready" ? t("workspace.online") : t("nodeHeader.connecting")}
                  </Text>
                </View>
              </View>
              <Text style={styles.activeNodeTitle} numberOfLines={1}>
                {gateway.nodeId ? activeNodeName : (cacheSnapshot?.nodes[0]?.display_name || t("common.unnamedComputer"))}
              </Text>
            </View>
            <View style={styles.launchButton}>
              <Text style={styles.launchButtonText}>{t("workspace.launchNode")}</Text>
              <AppIcon name="chevron-right" color={colors.accent} size={16} />
            </View>
          </AppPressable>
        ) : null}

        {/* Quick Node Switcher & Terminal Hub */}
        <View style={styles.sectionCard}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionHeaderTitleRow}>
              <AppIcon name="node" color={colors.accent} size={18} />
              <Text style={styles.sectionTitle}>{t("workspace.quickNodesTitle")}</Text>
              <View style={styles.countBadge}>
                <Text style={styles.countBadgeText}>{counts.onlineNodes}/{counts.nodes}</Text>
              </View>
            </View>
            <AppPressable
              style={styles.pairNewButton}
              onPress={() => router.push({ pathname: "/pair", params: routeParams })}
            >
              <AppIcon name="plus" color={colors.accent} size={16} />
              <Text style={styles.pairNewText}>{t("workspace.pairNewNode")}</Text>
            </AppPressable>
          </View>

          {cacheSnapshot?.nodes && cacheSnapshot.nodes.length > 0 ? (
            <View style={styles.nodeList}>
              {cacheSnapshot.nodes.map((node) => {
                const isCurrent = node.node_id === gateway.nodeId;
                const isSwitching = switchingNodeId === node.node_id;
                const nodeName = presentHubNodeName(node, t("common.unnamedComputer"));
                return (
                  <AppPressable
                    key={node.node_id}
                    style={[styles.nodeRow, isCurrent && styles.nodeRowCurrent]}
                    disabled={isSwitching}
                    onPress={() => void handleSwitchAndLaunch(node)}
                  >
                    <View style={[styles.nodeStatusDot, node.online ? styles.nodeStatusOnline : styles.nodeStatusOffline]} />
                    <View style={styles.flex}>
                      <Text style={[styles.nodeRowName, isCurrent && styles.nodeRowNameCurrent]} numberOfLines={1}>
                        {nodeName}
                      </Text>
                      <Text style={styles.nodeRowMeta}>
                        {node.online ? t("workspace.online") : t("workspace.offline")}
                        {node.version ? ` · v${node.version}` : ""}
                      </Text>
                    </View>
                    {isSwitching ? (
                      <ActivityIndicator size="small" color={colors.accent} />
                    ) : isCurrent ? (
                      <View style={styles.currentNodeTag}>
                        <Text style={styles.currentNodeTagText}>{t("nodeSwitch.currentNode")}</Text>
                      </View>
                    ) : (
                      <View style={styles.switchActionTag}>
                        <Text style={styles.switchActionText}>{t("workspace.switchAndLaunch")}</Text>
                      </View>
                    )}
                  </AppPressable>
                );
              })}
            </View>
          ) : !loading ? (
            <View style={styles.emptyNodesWrap}>
              <Text style={styles.emptyNodesText}>{t("workspace.noNodesHint")}</Text>
              <AppPressable
                style={styles.emptyPairButton}
                onPress={() => router.push({ pathname: "/pair", params: routeParams })}
              >
                <AppIcon name="camera" color={colors.onAccent} size={16} />
                <Text style={styles.emptyPairButtonText}>{t("workspace.pairNewNode")}</Text>
              </AppPressable>
            </View>
          ) : null}
        </View>

        {loading ? <AsyncStateView state="loading" /> : null}

        {/* Workspace Management Grid */}
        <View style={styles.managementGrid}>
          <Entry
            icon="chat"
            title={t("workspace.workTitle")}
            detail={counts.work ? t("workspace.workDetailCount", { count: counts.work }) : t("workspace.workDetailEmpty")}
            onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/work", params: routeParams })}
          />
          <Entry
            icon="agent"
            title={t("workspace.sharedResourcesTitle")}
            detail={counts.resources ? t("workspace.sharedResourcesCount", { count: counts.resources }) : t("workspace.sharedResourcesEmpty")}
            onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/resources", params: routeParams })}
          />
          <Entry
            icon="node"
            title={t("workspace.nodesTitle")}
            detail={t("workspace.nodesDetail", { total: counts.nodes, online: counts.onlineNodes })}
            onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })}
          />
          <Entry
            icon="user"
            title={t("workspace.membersTitle")}
            detail={t("workspace.membersDetail", { count: counts.members })}
            onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/members", params: routeParams })}
          />
        </View>

        {/* Celebration Banner after first pairing */}
        {!loading && showConnected ? (
          <Animated.View style={[styles.connectedCard, {
            opacity: celebration,
            transform: [{ scale: celebration.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1] }) }],
          }]}>
            <View style={styles.connectedIcon}>
              <AppIcon name="check" color={colors.onAccent} size={28} />
            </View>
            <Text style={styles.connectedTitle}>{t("workspace.connectedTitle")}</Text>
            <Text style={styles.connectedDetail}>{t("workspace.connectedDetail")}</Text>
            {gateway.status === "ready" ? (
              <Text style={styles.connectedHealthHint}>{t("workspace.connectedHealthCheck")}</Text>
            ) : null}
            <AppPressable
              style={[styles.connectedAction, gateway.status !== "ready" && styles.connectedActionDisabled]}
              disabled={gateway.status !== "ready"}
              onPress={() => router.push({ pathname: "/(tabs)", params: { workspaceId, workspaceName: displayName, nodeId: gateway.nodeId } })}
            >
              {gateway.status === "ready" ? (
                <Text style={styles.connectedActionText}>{t("workspace.startChatting")}</Text>
              ) : (
                <ActivityIndicator color={colors.accent} size="small" />
              )}
            </AppPressable>
          </Animated.View>
        ) : null}

        {/* Onboarding Guide if no nodes connected */}
        {!loading && counts.nodes === 0 && !showConnected ? (
          <View style={styles.onboarding}>
            <Text style={styles.sectionTitle}>{t("workspace.connectComputer")}</Text>
            <OnboardingStep
              number="1"
              title={t("workspace.onboardingInstallTitle")}
              detail={t("workspace.onboardingInstallDetail")}
              action={(
                <AppPressable style={styles.secondary} onPress={() => void Linking.openURL(downloadUrl)}>
                  <AppIcon name="share" color={colors.accent} size={16} />
                  <Text style={styles.secondaryText}>{t("workspace.onboardingDownload")}</Text>
                </AppPressable>
              )}
            />
            <OnboardingStep
              number="2"
              title={t("workspace.onboardingScanTitle")}
              detail={t("workspace.onboardingScanDetail")}
              action={(
                <AppPressable style={styles.primary} disabled={connectWorking} onPress={() => void startConnectFlow()}>
                  {connectWorking ? (
                    <ActivityIndicator color={colors.onAccent} size="small" />
                  ) : (
                    <>
                      <AppIcon name="camera" color={colors.onAccent} size={18} />
                      <Text style={styles.primaryText}>{t("workspace.onboardingScanAction")}</Text>
                    </>
                  )}
                </AppPressable>
              )}
            />
            {connectMessage ? <Text style={styles.connectMessage}>{connectMessage}</Text> : null}
          </View>
        ) : null}

        {error ? (
          <AsyncStateView
            state="error"
            message={error}
            onRetry={() => void refresh()}
            retryLabel={t("common.refresh")}
          />
        ) : null}
      </ScrollView>
    </>
  );
}

function Entry({
  icon,
  title,
  detail,
  onPress,
}: {
  icon: AppIconName;
  title: string;
  detail: string;
  onPress(): void;
}) {
  return (
    <AppPressable style={styles.entry} onPress={onPress}>
      <View style={styles.entryIcon}>
        <AppIcon name={icon} color={colors.accent} size={22} />
      </View>
      <View style={styles.entryContent}>
        <Text style={styles.entryTitle}>{title}</Text>
        <Text style={styles.entryDetail} numberOfLines={1}>{detail}</Text>
      </View>
      <AppIcon name="chevron-right" color={colors.muted} size={18} />
    </AppPressable>
  );
}

function OnboardingStep({
  number,
  title,
  detail,
  action,
}: {
  number: string;
  title: string;
  detail: string;
  action: ReactNode;
}) {
  return (
    <View style={styles.step}>
      <View style={styles.stepNumber}>
        <Text style={styles.stepNumberText}>{number}</Text>
      </View>
      <View style={styles.flex}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.meta}>{detail}</Text>
        <View style={styles.stepAction}>{action}</View>
      </View>
    </View>
  );
}

function value(input: string | string[] | undefined) {
  return Array.isArray(input) ? input[0] ?? "" : input ?? "";
}

function roleLabel(role: HostedWorkspace["role"] | undefined, t: ReturnType<typeof useI18n>["t"]) {
  if (role === "member") return t("account.roleMember");
  if (role === "admin") return t("account.roleAdmin");
  return t("account.roleOwner");
}

const styles = StyleSheet.create({
  container: {
    padding: spacing.large,
    gap: spacing.medium,
    paddingBottom: 52,
    backgroundColor: colors.background,
  },
  hero: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    padding: spacing.large,
    borderRadius: radii.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  heroIcon: {
    width: 48,
    height: 48,
    borderRadius: radii.large,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accentSoft,
  },
  flex: {
    flex: 1,
    minWidth: 0,
  },
  title: {
    color: colors.ink,
    ...typography.heading,
  },
  meta: {
    color: colors.muted,
    ...typography.small,
    lineHeight: 18,
  },
  iconButton: {
    width: 42,
    height: 42,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.pill,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.line,
  },
  activeNodeCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    padding: spacing.large,
    borderRadius: radii.large,
    backgroundColor: colors.accent,
    ...shadows.card,
  },
  activeNodeIcon: {
    width: 46,
    height: 46,
    borderRadius: radii.medium,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255, 255, 255, 0.2)",
  },
  activeNodeHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    marginBottom: 2,
  },
  activeNodeLabel: {
    color: colors.onAccent,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    opacity: 0.85,
  },
  activeStatusPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "rgba(255, 255, 255, 0.22)",
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radii.pill,
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  statusDotOnline: {
    backgroundColor: "#10B981",
  },
  statusDotOffline: {
    backgroundColor: colors.onAccent,
    opacity: 0.6,
  },
  activeStatusText: {
    color: colors.onAccent,
    fontSize: 10,
    fontWeight: "700",
  },
  activeNodeTitle: {
    color: colors.onAccent,
    fontSize: 16,
    fontWeight: "800",
  },
  launchButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
  },
  launchButtonText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "800",
  },
  sectionCard: {
    padding: spacing.large,
    borderRadius: radii.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    gap: spacing.medium,
    ...shadows.card,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  sectionHeaderTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "800",
  },
  countBadge: {
    backgroundColor: colors.accentSoft,
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radii.pill,
  },
  countBadgeText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
  },
  pairNewButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 2,
    paddingHorizontal: 8,
    borderRadius: radii.pill,
    backgroundColor: colors.accentSoft,
  },
  pairNewText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  nodeList: {
    gap: spacing.small,
  },
  nodeRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
  },
  nodeRowCurrent: {
    borderColor: colors.accent,
    backgroundColor: colors.accentSoft,
  },
  nodeStatusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  nodeStatusOnline: {
    backgroundColor: "#10B981",
  },
  nodeStatusOffline: {
    backgroundColor: colors.muted,
  },
  nodeRowName: {
    color: colors.ink,
    fontSize: 14,
    fontWeight: "700",
  },
  nodeRowNameCurrent: {
    color: colors.accent,
    fontWeight: "800",
  },
  nodeRowMeta: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 1,
  },
  currentNodeTag: {
    paddingHorizontal: spacing.small,
    paddingVertical: 3,
    borderRadius: radii.pill,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.accent,
  },
  currentNodeTagText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
  },
  switchActionTag: {
    paddingHorizontal: spacing.small,
    paddingVertical: 4,
    borderRadius: radii.pill,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  switchActionText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600",
  },
  emptyNodesWrap: {
    paddingVertical: spacing.large,
    alignItems: "center",
    gap: spacing.medium,
  },
  emptyNodesText: {
    color: colors.muted,
    fontSize: 13,
  },
  emptyPairButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.accent,
  },
  emptyPairButtonText: {
    color: colors.onAccent,
    fontSize: 13,
    fontWeight: "700",
  },
  managementGrid: {
    gap: spacing.small,
  },
  entry: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    padding: spacing.medium,
    borderRadius: radii.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  entryIcon: {
    width: 42,
    height: 42,
    borderRadius: radii.medium,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accentSoft,
  },
  entryContent: {
    flex: 1,
    minWidth: 0,
  },
  entryTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "700",
  },
  entryDetail: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2,
  },
  connectedCard: {
    alignItems: "center",
    gap: spacing.small,
    padding: spacing.xlarge,
    borderRadius: radii.large,
    backgroundColor: colors.accent,
    ...shadows.card,
  },
  connectedIcon: {
    width: 58,
    height: 58,
    borderRadius: radii.large,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.18)",
  },
  connectedTitle: {
    color: colors.onAccent,
    fontSize: 22,
    fontWeight: "800",
  },
  connectedDetail: {
    color: colors.onAccent,
    opacity: 0.9,
    fontSize: 15,
    lineHeight: 22,
    textAlign: "center",
  },
  connectedAction: {
    marginTop: spacing.medium,
    minHeight: 46,
    minWidth: 160,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.medium,
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.large,
  },
  connectedActionText: {
    color: colors.accent,
    fontWeight: "800",
    fontSize: 15,
  },
  connectedActionDisabled: {
    opacity: 0.72,
  },
  connectedHealthHint: {
    color: colors.onAccent,
    opacity: 0.86,
    ...typography.small,
    lineHeight: 18,
    textAlign: "center",
  },
  onboarding: {
    padding: spacing.large,
    gap: spacing.medium,
    borderRadius: radii.large,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  step: {
    flexDirection: "row",
    gap: spacing.medium,
    alignItems: "flex-start",
  },
  stepNumber: {
    width: 28,
    height: 28,
    borderRadius: radii.pill,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accentSoft,
  },
  stepNumberText: {
    color: colors.accent,
    fontWeight: "800",
    fontSize: 13,
  },
  stepAction: {
    marginTop: spacing.small,
  },
  rowTitle: {
    color: colors.ink,
    fontWeight: "800",
    fontSize: 14,
  },
  secondary: {
    minHeight: 38,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.small,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.accent,
    paddingHorizontal: spacing.medium,
  },
  secondaryText: {
    color: colors.accent,
    fontWeight: "700",
    fontSize: 13,
  },
  primary: {
    minHeight: 40,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.medium,
  },
  primaryText: {
    color: colors.onAccent,
    fontWeight: "700",
    fontSize: 13,
  },
  connectMessage: {
    color: colors.accent,
    ...typography.small,
    lineHeight: 18,
  },
});
