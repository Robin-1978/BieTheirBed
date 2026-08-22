import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { WorkspaceCacheBanner } from "@/components/WorkspaceCacheBanner";
import {
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
import { useGateway } from "@/state/GatewayProvider";
import { loadWorkspaceCache, mergeWorkspaceCache, type WorkspaceCacheSnapshot } from "@/storage/workspaceCache";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

export default function WorkspaceScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
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
    } catch (caught) { setError(caught instanceof Error ? caught.message : t("workspace.loadFailed")); }
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
            ? { pathname: "/chat", params: { workspaceId, workspaceName: displayName, nodeId: gateway.nodeId } }
            : { pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })}
        >
          <AppIcon name="chat" color={colors.white} size={22} />
          <View style={styles.flex}>
            <Text style={styles.startWorkTitle}>{t("workspace.startWork")}</Text>
            <Text style={styles.startWorkDetail}>{gateway.status === "ready" ? t("workspace.startWorkReady") : t("workspace.startWorkConnect")}</Text>
          </View>
          <AppIcon name="chevron-right" color={colors.white} size={19} />
        </AppPressable>
        {loading ? <AsyncStateView state="loading" /> : null}

        <View style={styles.grid}>
          <Entry icon="chat" title={t("workspace.workTitle")} detail={counts.work ? t("workspace.workDetailCount", { count: counts.work }) : t("workspace.workDetailEmpty")} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/work", params: routeParams })} />
          <Entry icon="agent" title={t("workspace.sharedResourcesTitle")} detail={counts.resources ? t("workspace.sharedResourcesCount", { count: counts.resources }) : t("workspace.sharedResourcesEmpty")} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/resources", params: routeParams })} />
          <Entry icon="node" title={t("workspace.nodesTitle")} detail={t("workspace.nodesDetail", { total: counts.nodes, online: counts.onlineNodes })} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })} />
          <Entry icon="user" title={t("workspace.membersTitle")} detail={t("workspace.membersDetail", { count: counts.members })} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/members", params: routeParams })} />
        </View>

        {!loading && counts.nodes === 0 ? (
          <View style={styles.onboarding}>
            <Text style={styles.sectionTitle}>{t("workspace.getStarted")}</Text>
            <Step number="1" title={t("workspace.step1Title")} detail={t("workspace.step1Detail")} />
            <Step number="2" title={t("workspace.step2Title")} detail={t("workspace.step2Detail")} />
            <Step number="3" title={t("workspace.step3Title")} detail={t("workspace.step3Detail")} />
            <AppPressable style={styles.primary} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })}><Text style={styles.primaryText}>{t("workspace.addNode")}</Text></AppPressable>
          </View>
        ) : null}
        {error ? <AsyncStateView state="error" message={error} onRetry={() => void refresh()} retryLabel={t("common.refresh")} /> : null}
      </ScrollView>
    </>
  );
}

function Entry({ icon, title, detail, onPress }: { icon: AppIconName; title: string; detail: string; onPress(): void }) { return <AppPressable style={styles.entry} onPress={onPress}><View style={styles.entryIcon}><AppIcon name={icon} color={colors.accent} size={24} /></View><Text style={styles.entryTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text><AppIcon name="chevron-right" color={colors.muted} size={18} /></AppPressable>; }
function Step({ number, title, detail }: { number: string; title: string; detail: string }) { return <View style={styles.step}><View style={styles.stepNumber}><Text style={styles.stepNumberText}>{number}</Text></View><View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View></View>; }
function value(input: string | string[] | undefined) { return Array.isArray(input) ? input[0] ?? "" : input ?? ""; }
function roleLabel(role: HostedWorkspace["role"] | undefined, t: ReturnType<typeof useI18n>["t"]) {
  if (role === "member") return t("account.roleMember");
  if (role === "admin") return t("account.roleAdmin");
  return t("account.roleOwner");
}
const styles = StyleSheet.create({
  container: { padding: 17, gap: 14, paddingBottom: 52 }, hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, heroIcon: { width: 50, height: 50, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 20, fontWeight: "800" }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" }, grid: { gap: 10 }, entry: { minHeight: 82, flexDirection: "row", alignItems: "center", gap: 12, padding: 14, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, entryIcon: { width: 46, height: 46, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, entryTitle: { color: colors.ink, fontSize: 16, fontWeight: "800", minWidth: 70 }, onboarding: { padding: 16, gap: 12, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "800" }, step: { flexDirection: "row", gap: 11, alignItems: "center" }, stepNumber: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, stepNumberText: { color: colors.accent, fontWeight: "800" }, rowTitle: { color: colors.ink, fontWeight: "800" }, primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.accent }, primaryText: { color: colors.white, fontWeight: "800" },
  startWork: { minHeight: 72, flexDirection: "row", alignItems: "center", gap: 11, padding: 15, borderRadius: 17, backgroundColor: colors.accent }, startWorkTitle: { color: colors.white, fontSize: 16, fontWeight: "800" }, startWorkDetail: { color: colors.white, opacity: 0.86, fontSize: 12, lineHeight: 17 },
});
