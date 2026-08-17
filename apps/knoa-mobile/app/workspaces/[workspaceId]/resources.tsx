import { Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { loadWorkspaceResourceState, type WorkspaceResourceState } from "@/hub/hubClient";
import { colors } from "@/theme";

export default function WorkspaceResourcesScreen() {
  const [state, setState] = useState<WorkspaceResourceState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setState(await loadWorkspaceResourceState()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "资源加载失败"); }
    finally { setLoading(false); }
  }, []);
  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));
  const sharedResources = state?.workspaceResources.filter((resource) => resource.kind === "model" || resource.kind === "mcp") ?? [];
  const sharedDeployments = state?.workspaceDeployments.filter((deployment) => deployment.kind === "model" || deployment.kind === "mcp") ?? [];
  return (
    <>
      <Stack.Screen options={{ title: "共享服务" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.header}><View style={styles.icon}><AppIcon name="agent" color={colors.accent} size={27} /></View><View style={styles.flex}><Text style={styles.title}>跨 Node 共享服务</Text><Text style={styles.meta}>这里只管理明确发布的 LLM 与 MCP Endpoint；Agent、Conversation、Task、Tool 和 Secret 均属于具体 Node。</Text></View><AppPressable onPress={() => void refresh()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable></View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}
        <Section title="服务目录">
          {sharedResources.map((resource) => <Row key={resource.resource_id} title={resource.display_name} detail={`${resource.kind.toUpperCase()} · Generation ${resource.generation} · ${resource.enabled ? "已发布" : "已停用"}`} />)}
          {!loading && !sharedResources.length ? <Text style={styles.meta}>还没有共享 LLM/MCP 服务。</Text> : null}
        </Section>
        <Section title="Endpoint">
          {sharedDeployments.map((deployment) => <Row key={deployment.deployment_id} title={deployment.deployment_id} detail={`${deployment.kind.toUpperCase()} → ${deployment.target_node_id} · Desired ${deployment.desired_generation}`} />)}
          {!loading && !sharedDeployments.length ? <Text style={styles.meta}>还没有发布 Endpoint。请先在资源所在 Node 配置 Model 或 MCP。</Text> : null}
        </Section>
        <Section title="远程授权">
          {state?.grants.map((grant) => <Row key={grant.grant_id} title={grant.grant_id} detail={`${grant.caller_node_id} → ${grant.target_deployment_id}`} />)}
          {!loading && !state?.grants.length ? <Text style={styles.meta}>跨 Node LLM/MCP 调用必须显式授权；MCP 默认不共享，Secret 始终保留在资源所在 Node。</Text> : null}
        </Section>
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </>
  );
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) { return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>; }
function Row({ title, detail }: { title: string; detail: string }) { return <View style={styles.row}><View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View></View>; }
const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 48 }, header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, icon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 19, fontWeight: "800" }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" }, section: { padding: 16, gap: 10, borderRadius: 18, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface }, sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800" }, row: { paddingTop: 10, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line }, rowTitle: { color: colors.ink, fontWeight: "800" }, error: { color: colors.danger },
});
