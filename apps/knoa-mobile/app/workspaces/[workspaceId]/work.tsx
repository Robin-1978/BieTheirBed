import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { listHubNodes, listWorkspaceWork, type HubNode, type WorkspaceWorkProjection } from "@/hub/hubClient";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function WorkspaceWorkScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const [items, setItems] = useState<WorkspaceWorkProjection[]>([]);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [work, directory] = await Promise.all([listWorkspaceWork(), listHubNodes()]);
      setItems(work);
      setNodes(directory);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace 工作目录加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));

  async function open(item: WorkspaceWorkProjection) {
    const node = nodes.find((value) => value.node_id === item.node_id);
    const bound = gateway.nodes.some((value) => value.nodeId === item.node_id);
    if (!node?.online || !bound) {
      setError(!node?.online ? "权威 Node 当前离线；Workspace 中仍可查看最后同步状态" : "请先将此 App 与权威 Node 配对");
      return;
    }
    setWorking(item.entity_id);
    setError("");
    try {
      await gateway.switchNode(item.node_id);
      const routeParams = {
        workspaceId: params.workspaceId,
        workspaceName: params.workspaceName ?? "Workspace",
        nodeId: item.node_id,
      };
      if (item.entity_kind === "conversation") {
        await gateway.openConversation(item.entity_id);
        router.push({ pathname: "/chat", params: routeParams });
      } else {
        router.push({ pathname: "/tasks/[id]", params: { ...routeParams, id: item.entity_id } });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法连接权威 Node");
    } finally {
      setWorking("");
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "工作" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.header}>
          <View style={styles.headerIcon}><AppIcon name="chat" color={colors.accent} size={27} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>Workspace 工作目录</Text>
            <Text style={styles.meta}>会话与任务在 Node 执行；这里保存跨 Node 可见的管理投影。</Text>
          </View>
          <AppPressable accessibilityLabel="刷新" onPress={() => void refresh()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable>
        </View>
        <View style={styles.actions}>
          <AppPressable style={styles.primary} onPress={() => router.push({ pathname: "/conversations", params: { workspaceId: params.workspaceId, workspaceName: params.workspaceName ?? "" } })}><Text style={styles.primaryText}>当前 Node 会话</Text></AppPressable>
          <AppPressable style={styles.secondary} onPress={() => router.push({ pathname: "/tasks", params: { workspaceId: params.workspaceId, workspaceName: params.workspaceName ?? "" } })}><Text style={styles.secondaryText}>当前 Node 任务</Text></AppPressable>
        </View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}
        {!loading && !items.length ? <View style={styles.empty}><Text style={styles.itemTitle}>还没有同步的工作</Text><Text style={styles.meta}>连接 Node 并创建会话或部署任务后，状态会同步到这里；Node 离线不会阻塞 Workspace 管理。</Text></View> : null}
        {items.map((item) => {
          const node = nodes.find((value) => value.node_id === item.node_id);
          return (
            <AppPressable key={`${item.entity_kind}:${item.entity_id}`} style={styles.card} onPress={() => void open(item)}>
              <View style={styles.row}>
                <AppIcon name={item.entity_kind === "conversation" ? "chat" : "tasks"} color={colors.accent} size={22} />
                <View style={styles.flex}>
                  <Text style={styles.itemTitle}>{item.title || item.entity_id}</Text>
                  <Text style={styles.meta}>{item.entity_kind === "conversation" ? "会话" : "任务"} · {node?.display_name ?? item.node_id} · {item.state}</Text>
                </View>
                {working === item.entity_id ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={node?.online ? styles.online : styles.offline}>{node?.online ? "在线" : "离线"}</Text>}
              </View>
              {item.summary ? <Text style={styles.summary} numberOfLines={3}>{item.summary}</Text> : null}
              {item.approval_summary ? <Text style={styles.approval}>{item.approval_summary}</Text> : null}
            </AppPressable>
          );
        })}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 12, paddingBottom: 48 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  headerIcon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  actions: { flexDirection: "row", gap: 9 },
  primary: { flex: 1, minHeight: 44, borderRadius: 13, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  primaryText: { color: colors.white, fontWeight: "800" },
  secondary: { flex: 1, minHeight: 44, borderRadius: 13, borderWidth: 1, borderColor: colors.accent, alignItems: "center", justifyContent: "center" },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  empty: { padding: 18, gap: 7, borderRadius: 16, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  card: { padding: 15, gap: 9, borderRadius: 17, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  row: { flexDirection: "row", alignItems: "center", gap: 11 },
  itemTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  summary: { color: colors.ink, lineHeight: 20 },
  approval: { color: colors.warning, fontSize: 12, fontWeight: "700" },
  online: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  offline: { color: colors.muted, fontWeight: "700", fontSize: 12 },
  error: { color: colors.danger, lineHeight: 20 },
});
