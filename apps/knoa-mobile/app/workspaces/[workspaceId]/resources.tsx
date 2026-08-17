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
  return (
    <>
      <Stack.Screen options={{ title: "资源与部署" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.header}><View style={styles.icon}><AppIcon name="agent" color={colors.accent} size={27} /></View><View style={styles.flex}><Text style={styles.title}>Workspace 共享资源</Text><Text style={styles.meta}>Agent、Runtime、Model、Skill、MCP 与 Policy 在 Workspace 定义，再部署到目标 Node。</Text></View><AppPressable onPress={() => void refresh()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable></View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}
        <Section title="资源定义">
          {state?.workspaceResources.map((resource) => <Row key={resource.resource_id} title={resource.display_name} detail={`${resource.kind} · Generation ${resource.generation} · ${resource.enabled ? "已发布" : "已停用"}`} />)}
          {state?.resources.map((resource) => <Row key={`model:${resource.resource_id}`} title={resource.display_name} detail={`model · ${resource.provider_protocol} · ${resource.model_identity}`} />)}
          {!loading && !(state?.workspaceResources.length || state?.resources.length) ? <Text style={styles.meta}>还没有 Workspace 资源。</Text> : null}
        </Section>
        <Section title="部署">
          {state?.workspaceDeployments.map((deployment) => <Row key={deployment.deployment_id} title={deployment.deployment_id} detail={`${deployment.kind} → ${deployment.target_node_id} · Desired ${deployment.desired_generation}`} />)}
          {state?.deployments.map((deployment) => <Row key={`legacy:${deployment.deployment_id}`} title={deployment.deployment_id} detail={`model → ${deployment.target_node_id} · Desired ${deployment.desired_revision}`} />)}
          {!loading && !(state?.workspaceDeployments.length || state?.deployments.length) ? <Text style={styles.meta}>还没有部署。Task 在启用、定时或执行前也必须部署到一个 Node。</Text> : null}
        </Section>
        <Section title="远程授权">
          {state?.grants.map((grant) => <Row key={grant.grant_id} title={grant.grant_id} detail={`${grant.caller_node_id} → ${grant.target_deployment_id}`} />)}
          {!loading && !state?.grants.length ? <Text style={styles.meta}>跨 Node 调用必须显式授权；Secret 始终保留在资源所在 Node。</Text> : null}
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
