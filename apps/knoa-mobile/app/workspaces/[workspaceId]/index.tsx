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
} from "@/hub/hubClient";
import { rememberWorkspace } from "@/navigation/navigationPreference";
import { triggerWelcomeHealthCheck } from "@/onboarding/firstConnect";
import { useGateway } from "@/state/GatewayProvider";
import { loadWorkspaceCache, mergeWorkspaceCache, type WorkspaceCacheSnapshot } from "@/storage/workspaceCache";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { userFacingError } from "@/ui/userFacingError";

const HOSTED_HUB_URL = "https://knoa.tinydotdot.com";

export default function WorkspaceScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string; connected?: string }>();
  const workspaceId = value(params.workspaceId);
  const { t } = useI18n();
  const fallbackName = value(params.workspaceName) || t("nav.workspace");
  const gateway = useGateway();
  const taskRoute = (template: string) => ({
    pathname: "/tasks/new" as const,
    params: { template, workspaceId, workspaceName: displayName, nodeId: gateway.nodeId },
  });
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

  return (
    <>
      <Stack.Screen options={{ title: displayName }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="workspace" color={colors.accent} size={29} /></View>
          <View style={styles.flex}><Text style={styles.title}>{displayName}</Text><Text style={styles.meta}>{workspace?.kind === "shared" ? t("workspace.shared") : t("workspace.personal")} · {roleLabel(workspace?.role, t)}</Text></View>
          <AppPressable accessibilityLabel={t("workspace.accountHome")} onPress={() => router.push("/account")} style={styles.iconButton}><AppIcon name="user" color={colors.muted} size={25} /></AppPressable>
        </View>
        <WorkspaceCacheBanner snapshot={cacheSnapshot} loading={refreshing} error={error} onRefresh={() => void refresh()} />
        <AppPressable
          style={styles.startWork}
          onPress={() => router.push(gateway.status === "ready"
            ? { pathname: "/(tabs)", params: { workspaceId, workspaceName: displayName, nodeId: gateway.nodeId } }
            : { pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })}
        >
          <AppIcon name="chat" color={colors.onAccent} size={22} />
          <View style={styles.flex}>
            <Text style={styles.startWorkTitle}>{t("workspace.startWork")}</Text>
            <Text style={styles.startWorkDetail}>{gateway.status === "ready" ? t("workspace.startWorkReady") : t("workspace.startWorkConnect")}</Text>
          </View>
          <AppIcon name="chevron-right" color={colors.onAccent} size={19} />
        </AppPressable>
        <View style={styles.capabilityCard}>
          <Text style={styles.sectionTitle}>{t("workspace.capabilitiesTitle")}</Text>
          <Text style={styles.meta}>{t("workspace.capabilitiesDetail")}</Text>
          <View style={styles.capabilityGrid}>
            <Capability icon="file" title={t("taskTemplates.folderTitle")} onPress={() => router.push(taskRoute("folder-organizer"))} />
            <Capability icon="settings" title={t("taskTemplates.healthTitle")} onPress={() => router.push(taskRoute("computer-health"))} />
            <Capability icon="file" title={t("taskTemplates.documentTitle")} onPress={() => router.push(taskRoute("document-digest"))} />
            <Capability icon="agent" title={t("taskTemplates.projectTitle")} onPress={() => router.push(taskRoute("project-maintenance"))} />
            <Capability icon="alert" title={t("taskTemplates.serviceTitle")} onPress={() => router.push(taskRoute("service-monitor"))} />
            <Capability icon="history" title={t("taskTemplates.summaryTitle")} onPress={() => router.push(taskRoute("work-summary"))} />
            <Capability icon="chat" title={t("taskTemplates.researchTitle")} onPress={() => router.push(taskRoute("research-brief"))} />
          </View>
        </View>
        {loading ? <AsyncStateView state="loading" /> : null}

        <View style={styles.grid}>
          <Entry icon="chat" title={t("workspace.workTitle")} detail={counts.work ? t("workspace.workDetailCount", { count: counts.work }) : t("workspace.workDetailEmpty")} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/work", params: routeParams })} />
          <Entry icon="agent" title={t("workspace.sharedResourcesTitle")} detail={counts.resources ? t("workspace.sharedResourcesCount", { count: counts.resources }) : t("workspace.sharedResourcesEmpty")} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/resources", params: routeParams })} />
          <Entry icon="node" title={t("workspace.nodesTitle")} detail={t("workspace.nodesDetail", { total: counts.nodes, online: counts.onlineNodes })} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })} />
          <Entry icon="user" title={t("workspace.membersTitle")} detail={t("workspace.membersDetail", { count: counts.members })} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/members", params: routeParams })} />
        </View>

        {!loading && showConnected ? (
          <Animated.View style={[styles.connectedCard, {
            opacity: celebration,
            transform: [{ scale: celebration.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1] }) }],
          }]}>
            <View style={styles.connectedIcon}><AppIcon name="check" color={colors.onAccent} size={28} /></View>
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
              {gateway.status === "ready"
                ? <Text style={styles.connectedActionText}>{t("workspace.startChatting")}</Text>
                : <ActivityIndicator color={colors.accent} size="small" />}
            </AppPressable>
          </Animated.View>
        ) : null}

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
                  {connectWorking
                    ? <ActivityIndicator color={colors.onAccent} size="small" />
                    : (
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
        {error ? <AsyncStateView state="error" message={error} onRetry={() => void refresh()} retryLabel={t("common.refresh")} /> : null}
      </ScrollView>
    </>
  );
}

function Entry({ icon, title, detail, onPress }: { icon: AppIconName; title: string; detail: string; onPress(): void }) { return <AppPressable style={styles.entry} onPress={onPress}><View style={styles.entryIcon}><AppIcon name={icon} color={colors.accent} size={24} /></View><Text style={styles.entryTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text><AppIcon name="chevron-right" color={colors.muted} size={18} /></AppPressable>; }
function Capability({ icon, title, onPress }: { icon: AppIconName; title: string; onPress(): void }) { return <AppPressable style={styles.capability} onPress={onPress}><AppIcon name={icon} color={colors.accent} size={20} /><Text style={styles.capabilityTitle} numberOfLines={2}>{title}</Text></AppPressable>; }
function OnboardingStep({ number, title, detail, action }: { number: string; title: string; detail: string; action: ReactNode }) {
  return (
    <View style={styles.step}>
      <View style={styles.stepNumber}><Text style={styles.stepNumberText}>{number}</Text></View>
      <View style={styles.flex}>
        <Text style={styles.rowTitle}>{title}</Text>
        <Text style={styles.meta}>{detail}</Text>
        <View style={styles.stepAction}>{action}</View>
      </View>
    </View>
  );
}
function value(input: string | string[] | undefined) { return Array.isArray(input) ? input[0] ?? "" : input ?? ""; }
function roleLabel(role: HostedWorkspace["role"] | undefined, t: ReturnType<typeof useI18n>["t"]) {
  if (role === "member") return t("account.roleMember");
  if (role === "admin") return t("account.roleAdmin");
  return t("account.roleOwner");
}
const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.large, paddingBottom: 52 }, hero: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card }, heroIcon: { width: 50, height: 50, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, ...typography.heading }, meta: { color: colors.muted, ...typography.small, lineHeight: 18 }, iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" }, grid: { gap: spacing.medium }, entry: { minHeight: 82, flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card }, entryIcon: { width: 46, height: 46, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, entryTitle: { color: colors.ink, fontSize: 16, fontWeight: "800", minWidth: 70 }, capabilityCard: { padding: spacing.large, gap: spacing.small, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card }, capabilityGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.small, marginTop: spacing.xsmall }, capability: { width: "48%", minHeight: 58, flexDirection: "row", alignItems: "center", gap: spacing.small, padding: spacing.medium, borderRadius: radii.medium, backgroundColor: colors.background },   capabilityTitle: { flex: 1, color: colors.ink, ...typography.small, fontWeight: "800" },
  onboarding: { padding: spacing.large, gap: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  connectedCard: { alignItems: "center", gap: spacing.small, padding: spacing.xlarge, borderRadius: radii.large, backgroundColor: colors.accent, ...shadows.card },
  connectedIcon: { width: 58, height: 58, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.18)" },
  connectedTitle: { color: colors.onAccent, fontSize: 22, fontWeight: "800" },
  connectedDetail: { color: colors.onAccent, opacity: 0.9, fontSize: 15, lineHeight: 22, textAlign: "center" },
  connectedAction: { marginTop: spacing.medium, minHeight: 48, minWidth: 180, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.surface, paddingHorizontal: spacing.large },
  connectedActionText: { color: colors.accent, fontWeight: "800", fontSize: 16 },
  connectedActionDisabled: { opacity: 0.72 },
  connectedHealthHint: { color: colors.onAccent, opacity: 0.86, ...typography.small, lineHeight: 18, textAlign: "center" },
  connectMessage: { color: colors.accent, ...typography.small, lineHeight: 18 },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "800" },
  step: { flexDirection: "row", gap: spacing.medium, alignItems: "flex-start" },
  stepNumber: { width: 30, height: 30, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  stepNumberText: { color: colors.accent, fontWeight: "800" },
  stepAction: { marginTop: spacing.small },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  secondary: { minHeight: 42, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.accent, paddingHorizontal: spacing.medium },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  primary: { minHeight: 46, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small, borderRadius: radii.medium, backgroundColor: colors.accent, paddingHorizontal: spacing.medium },
  primaryText: { color: colors.onAccent, fontWeight: "800" },
  startWork: { minHeight: 72, flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.accent }, startWorkTitle: { color: colors.onAccent, fontSize: 16, fontWeight: "800" }, startWorkDetail: { color: colors.onAccent, opacity: 0.86, fontSize: 12, lineHeight: 17 },
});
