import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  listHostedWorkspaceMembers,
  listHostedWorkspaces,
  listHubNodes,
  listWorkspaceWork,
  loadHubConnection,
  loadWorkspaceResourceState,
  selectHostedWorkspace,
  type HubNode,
  type HostedWorkspace,
} from "@/hub/hubClient";
import { rememberNodePage, rememberWorkspace } from "@/navigation/navigationPreference";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function WorkspaceScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  const fallbackName = stringParam(params.workspaceName) || "Workspace";
  const gateway = useGateway();
  const [workspace, setWorkspace] = useState<HostedWorkspace | null>(null);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [memberCount, setMemberCount] = useState(0);
  const [resourceCount, setResourceCount] = useState(0);
  const [deploymentCount, setDeploymentCount] = useState(0);
  const [workCount, setWorkCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const connection = await loadHubConnection();
      if (!connection) {
        router.replace("/account/login");
        return;
      }
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
      const [directory, members, resources, work] = await Promise.all([
        listHubNodes(),
        connection.accountId ? listHostedWorkspaceMembers(target.workspaceId).catch(() => []) : Promise.resolve([]),
        loadWorkspaceResourceState().catch(() => null),
        listWorkspaceWork().catch(() => []),
      ]);
      setNodes(directory);
      setMemberCount(members.length);
      setResourceCount((resources?.workspaceResources.length ?? 0) + (resources?.resources.length ?? 0));
      setDeploymentCount((resources?.workspaceDeployments.length ?? 0) + (resources?.deployments.length ?? 0));
      setWorkCount(work.length);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace 加载失败");
    } finally {
      setLoading(false);
    }
  }, [fallbackName, gateway.disconnectNode, workspaceId]);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));

  async function enterNode(node: HubNode) {
    const binding = gateway.nodes.find((item) => item.nodeId === node.node_id);
    if (!binding) {
      router.push({ pathname: "/pair", params: { workspaceId, workspaceName: workspace?.displayName || fallbackName } });
      return;
    }
    if (!node.online) {
      setError(`${node.display_name} 当前离线，请选择其他 Node`);
      return;
    }
    setWorking(node.node_id);
    setError("");
    try {
      await gateway.switchNode(node.node_id);
      const workspaceName = workspace?.displayName || fallbackName;
      await rememberNodePage({ workspaceId, workspaceName, nodeId: node.node_id, nodePage: "chat" });
      router.push({ pathname: "/chat", params: { workspaceId, workspaceName, nodeId: node.node_id } });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Node 连接失败");
    } finally {
      setWorking("");
    }
  }

  const displayName = workspace?.displayName || fallbackName;
  return (
    <>
      <Stack.Screen options={{ title: displayName }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="workspace" color={colors.accent} size={29} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{displayName}</Text>
            <Text style={styles.meta}>{workspace?.kind || "personal"} · {workspace?.role || "owner"}</Text>
          </View>
          <AppPressable accessibilityLabel="帐号首页" onPress={() => router.push("/account")} style={styles.iconButton}>
            <AppIcon name="user" color={colors.muted} size={25} />
          </AppPressable>
        </View>

        <View style={styles.metrics}>
          <Metric value={workCount} label="工作" />
          <Metric value={resourceCount} label="资源" />
          <Metric value={deploymentCount} label="部署" />
          <Metric value={nodes.length} label="Node" />
        </View>

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Node</Text>
          <AppPressable accessibilityLabel="刷新 Workspace" onPress={() => void refresh()} style={styles.iconButton}>
            <AppIcon name="refresh" color={colors.muted} size={19} />
          </AppPressable>
        </View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}
        {!loading && !nodes.length ? (
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>还没有 Node</Text>
            <Text style={styles.hint}>安装 Knoa Node 后扫描配对二维码。Workspace 和帐号管理不依赖 Node。</Text>
            <AppPressable style={styles.secondary} onPress={() => router.push({ pathname: "/pair", params: { workspaceId, workspaceName: displayName } })}>
              <Text style={styles.secondaryText}>添加第一台 Node</Text>
            </AppPressable>
          </View>
        ) : null}
        {nodes.map((node) => {
          const bound = gateway.nodes.some((item) => item.nodeId === node.node_id);
          return (
            <View key={node.node_id} style={styles.nodeCard}>
              <View style={styles.nodeIcon}><AppIcon name="node" color={node.online ? colors.accent : colors.muted} size={24} /></View>
              <View style={styles.flex}>
                <Text style={styles.nodeName}>{node.display_name}</Text>
                <Text style={styles.meta}>{node.platform} {node.version} · {bound ? "已绑定" : "未绑定"}</Text>
              </View>
              <View style={styles.nodeAction}>
                <Text style={node.online ? styles.online : styles.offline}>{node.online ? "在线" : "离线"}</Text>
                <AppPressable disabled={Boolean(working)} onPress={() => void enterNode(node)} style={styles.enterButton}>
                  {working === node.node_id ? <ActivityIndicator color="white" size="small" /> : <Text style={styles.enterText}>{bound ? "进入" : "配对"}</Text>}
                </AppPressable>
              </View>
            </View>
          );
        })}

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Workspace 管理</Text>
          <WorkspaceRow icon="chat" title="工作" detail={`${workCount} 个会话或任务投影`} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/work", params: { workspaceId, workspaceName: displayName } })} />
          <WorkspaceRow icon="agent" title="资源与部署" detail={`${resourceCount} 个资源 · ${deploymentCount} 个部署`} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/resources", params: { workspaceId, workspaceName: displayName } })} />
          <WorkspaceRow icon="node" title="Node 管理" detail={`${nodes.length} 个 Node · 配置与部署目标`} onPress={() => router.push({ pathname: "/workspaces/[workspaceId]/nodes", params: { workspaceId, workspaceName: displayName } })} />
          <WorkspaceRow icon="workspace" title="成员与权限" detail={`${memberCount} 个成员`} onPress={() => router.push("/account")} />
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </>
  );
}

function Metric({ value, label }: { value: number; label: string }) {
  return <View style={styles.metric}><Text style={styles.metricValue}>{value}</Text><Text style={styles.meta}>{label}</Text></View>;
}

function WorkspaceRow({ icon, title, detail, onPress }: { icon: "workspace" | "agent" | "chat" | "node"; title: string; detail: string; onPress(): void }) {
  return (
    <AppPressable style={styles.row} onPress={onPress}>
      <AppIcon name={icon} color={colors.accent} size={21} />
      <View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>
      <AppIcon name="chevron-right" color={colors.muted} size={18} />
    </AppPressable>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 48 },
  hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  heroIcon: { width: 50, height: 50, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  title: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  flex: { flex: 1, minWidth: 0 },
  meta: { color: colors.muted, fontSize: 12, marginTop: 2 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center", borderRadius: 13 },
  metrics: { flexDirection: "row", gap: 8 },
  metric: { flex: 1, alignItems: "center", paddingVertical: 13, borderRadius: 15, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  metricValue: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  sectionHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 4 },
  sectionTitle: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  nodeCard: { flexDirection: "row", alignItems: "center", gap: 11, padding: 14, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  nodeIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  nodeName: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  nodeAction: { alignItems: "flex-end", gap: 7 },
  online: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  offline: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  enterButton: { minWidth: 58, minHeight: 34, paddingHorizontal: 13, borderRadius: 11, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  enterText: { color: "white", fontWeight: "800", fontSize: 12 },
  empty: { padding: 20, gap: 10, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  emptyTitle: { color: colors.ink, fontSize: 17, fontWeight: "800" },
  hint: { color: colors.muted, lineHeight: 20 },
  secondary: { alignItems: "center", padding: 13, borderRadius: 13, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  card: { paddingHorizontal: 15, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800", marginTop: 12, marginBottom: 2 },
  row: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  error: { color: colors.danger, textAlign: "center", lineHeight: 20 },
});
