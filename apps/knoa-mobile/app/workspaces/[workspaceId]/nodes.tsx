import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { listHubNodes, loadWorkspaceResourceState, type HubNode, type WorkspaceDeployment } from "@/hub/hubClient";
import { useGateway } from "@/state/GatewayProvider";
import { updateNodeDirectGatewayUrl } from "@/security/deviceIdentity";
import { colors } from "@/theme";

export default function WorkspaceNodesScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [deployments, setDeployments] = useState<WorkspaceDeployment[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try { const [directory, resources] = await Promise.all([listHubNodes(), loadWorkspaceResourceState()]); setNodes(directory); setDeployments(resources.workspaceDeployments); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Node 加载失败"); }
    finally { setLoading(false); }
  }, []);
  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));
  async function enter(node: HubNode) {
    if (!node.online) { setError("Node 当前离线；这里只能查看最后同步状态，Agent、Conversation 和 Task 需要 Node 在线后管理"); return; }
    if (!gateway.nodes.some((item) => item.nodeId === node.node_id)) { router.push({ pathname: "/pair", params }); return; }
    setWorking(node.node_id);
    try {
      await updateNodeDirectGatewayUrl(node.node_id, node.direct_gateway_url || "");
      await gateway.switchNode(node.node_id);
      router.push({ pathname: "/node", params: { ...params, nodeId: node.node_id } });
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Node 连接失败"); }
    finally { setWorking(""); }
  }
  return (
    <>
      <Stack.Screen options={{ title: "Node 管理" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.header}><View style={styles.icon}><AppIcon name="node" color={colors.accent} size={27} /></View><View style={styles.flex}><Text style={styles.title}>Workspace Node</Text><Text style={styles.meta}>Agent、Conversation、Task、Tool 与 Secret 都属于具体 Node；Workspace 只管理 Enrollment、目录、共享 LLM/MCP 和状态投影。</Text></View><AppPressable onPress={() => void refresh()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable></View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}
        {nodes.map((node) => {
          const bound = gateway.nodes.some((item) => item.nodeId === node.node_id);
          const count = deployments.filter((item) => item.target_node_id === node.node_id).length;
          return <View key={node.node_id} style={styles.card}><View style={styles.row}><AppIcon name="node" color={node.online ? colors.accent : colors.muted} size={24} /><View style={styles.flex}><Text style={styles.nodeName}>{node.display_name}</Text><Text style={styles.meta}>{node.platform} {node.version} · {count} 个部署 · {bound ? "App 已配对" : "App 未配对"}</Text></View><Text style={node.online ? styles.online : styles.offline}>{node.online ? "在线" : "离线"}</Text></View><AppPressable disabled={Boolean(working)} onPress={() => void enter(node)} style={styles.enter}>{working === node.node_id ? <ActivityIndicator color={colors.white} size="small" /> : <Text style={styles.enterText}>{bound ? "进入 Node" : "配对 App"}</Text>}</AppPressable></View>;
        })}
        {!loading && !nodes.length ? <View style={styles.card}><Text style={styles.nodeName}>还没有 Node</Text><Text style={styles.meta}>先生成 Enrollment Grant，在电脑上安装并加入当前 Workspace。</Text><AppPressable style={styles.enter} onPress={() => router.push({ pathname: "/pair", params })}><Text style={styles.enterText}>添加 Node</Text></AppPressable></View> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </>
  );
}
const styles = StyleSheet.create({ container: { padding: 17, gap: 12, paddingBottom: 48 }, header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, icon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 19, fontWeight: "800" }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" }, card: { padding: 15, gap: 12, borderRadius: 17, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface }, row: { flexDirection: "row", alignItems: "center", gap: 11 }, nodeName: { color: colors.ink, fontSize: 16, fontWeight: "800" }, online: { color: colors.accent, fontWeight: "800", fontSize: 12 }, offline: { color: colors.muted, fontWeight: "700", fontSize: 12 }, enter: { minHeight: 42, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent }, enterText: { color: colors.white, fontWeight: "800" }, error: { color: colors.danger }, });
