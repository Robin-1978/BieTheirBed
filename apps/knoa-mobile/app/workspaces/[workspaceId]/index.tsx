import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
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
import { colors } from "@/theme";

export default function WorkspaceScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const workspaceId = value(params.workspaceId);
  const fallbackName = value(params.workspaceName) || "Workspace";
  const gateway = useGateway();
  const [workspace, setWorkspace] = useState<HostedWorkspace | null>(null);
  const [counts, setCounts] = useState({ work: 0, resources: 0, nodes: 0, onlineNodes: 0, members: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
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
      setWorkspace(target);
      await rememberWorkspace(target.workspaceId, target.displayName);
      const [nodes, resources, work, members] = await Promise.all([
        listHubNodes(),
        loadWorkspaceResourceState().catch(() => null),
        listWorkspaceWork().catch(() => []),
        connection.accountId ? listHostedWorkspaceMembers(target.workspaceId).catch(() => []) : Promise.resolve([]),
      ]);
      setCounts({
        work: work.length,
        resources: resources?.workspaceResources.filter((item) => item.enabled).length ?? 0,
        nodes: nodes.length,
        onlineNodes: nodes.filter((node) => node.online).length,
        members: members.length,
      });
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace 加载失败"); }
    finally { setLoading(false); }
  }, [fallbackName, gateway.disconnectNode, workspaceId]);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));
  const displayName = workspace?.displayName || fallbackName;
  const routeParams = { workspaceId, workspaceName: displayName };

  return (
    <>
      <Stack.Screen options={{ title: displayName }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="workspace" color={colors.accent} size={29} /></View>
          <View style={styles.flex}><Text style={styles.title}>{displayName}</Text><Text style={styles.meta}>{workspace?.kind === "shared" ? "共享 Workspace" : "个人 Workspace"} · {roleLabel(workspace?.role)}</Text></View>
          <AppPressable accessibilityLabel="帐号首页" onPress={() => router.push("/account")} style={styles.iconButton}><AppIcon name="user" color={colors.muted} size={25} /></AppPressable>
        </View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}

        <View style={styles.grid}>
          <Entry icon="chat" title="工作" detail={counts.work ? `${counts.work} 个最近会话与任务` : "跨 Node 查看会话与任务"} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/work", params: routeParams })} />
          <Entry icon="agent" title="共享资源" detail={counts.resources ? `${counts.resources} 个 LLM/MCP 服务` : "共享 Node 上的 LLM/MCP"} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/resources", params: routeParams })} />
          <Entry icon="node" title="Nodes" detail={`${counts.nodes} 台 · ${counts.onlineNodes} 台在线`} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })} />
          <Entry icon="user" title="成员" detail={`${counts.members} 个成员与权限`} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/members", params: routeParams })} />
        </View>

        {!loading && counts.nodes === 0 ? (
          <View style={styles.onboarding}>
            <Text style={styles.sectionTitle}>开始使用</Text>
            <Step number="1" title="添加第一台 Node" detail="在电脑安装 Knoa Node，并加入当前 Workspace。" />
            <Step number="2" title="配置模型" detail="连接本地 Qwen 或云端 LLM API。" />
            <Step number="3" title="开始对话或创建任务" detail="工作始终在你选择的 Node 上执行。" />
            <AppPressable style={styles.primary} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: routeParams })}><Text style={styles.primaryText}>添加 Node</Text></AppPressable>
          </View>
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </>
  );
}

function Entry({ icon, title, detail, onPress }: { icon: AppIconName; title: string; detail: string; onPress(): void }) { return <AppPressable style={styles.entry} onPress={onPress}><View style={styles.entryIcon}><AppIcon name={icon} color={colors.accent} size={24} /></View><Text style={styles.entryTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text><AppIcon name="chevron-right" color={colors.muted} size={18} /></AppPressable>; }
function Step({ number, title, detail }: { number: string; title: string; detail: string }) { return <View style={styles.step}><View style={styles.stepNumber}><Text style={styles.stepNumberText}>{number}</Text></View><View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View></View>; }
function value(input: string | string[] | undefined) { return Array.isArray(input) ? input[0] ?? "" : input ?? ""; }
function roleLabel(role?: HostedWorkspace["role"]) { return role === "member" ? "成员" : role === "admin" ? "管理员" : "所有者"; }
const styles = StyleSheet.create({
  container: { padding: 17, gap: 14, paddingBottom: 52 }, hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, heroIcon: { width: 50, height: 50, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 20, fontWeight: "800" }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" }, grid: { gap: 10 }, entry: { minHeight: 82, flexDirection: "row", alignItems: "center", gap: 12, padding: 14, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, entryIcon: { width: 46, height: 46, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, entryTitle: { color: colors.ink, fontSize: 16, fontWeight: "800", minWidth: 70 }, onboarding: { padding: 16, gap: 12, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "800" }, step: { flexDirection: "row", gap: 11, alignItems: "center" }, stepNumber: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, stepNumberText: { color: colors.accent, fontWeight: "800" }, rowTitle: { color: colors.ink, fontWeight: "800" }, primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.accent }, primaryText: { color: colors.white, fontWeight: "800" }, error: { color: colors.danger, lineHeight: 20 },
});
