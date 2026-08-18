import { router, Stack } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import type { ManagedConfig } from "@/api/models";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function NodeResourcesScreen() {
  const gateway = useGateway();
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [toolCount, setToolCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!gateway.client) return;
    setLoading(true);
    setError("");
    try {
      const [current, inventory] = await Promise.all([
        gateway.runAuthenticated((client) => client.getConfigCurrent()),
        gateway.sessionHandle
          ? gateway.runAuthenticated((client) => client.tools(gateway.sessionHandle))
          : Promise.resolve({ result: { descriptors: [] } }),
      ]);
      setDocument(current.revision.document);
      const result = inventory.result as { descriptors?: unknown[] };
      setToolCount(result.descriptors?.length ?? 0);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Node 资源加载失败");
    } finally {
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, gateway.sessionHandle]);

  useEffect(() => { void load(); }, [load]);

  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  const agents = document ? Object.entries(document.agents.agents) : [];
  const enabledAgents = agents.filter(([, agent]) => agent.enabled);
  const sharedModels = document
    ? Object.values(document.model_deployments).filter((deployment) => deployment.share_enabled).length
    : 0;

  return (
    <>
      <Stack.Screen options={{ title: "Node 资源" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="node" color={colors.accent} size={28} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{node?.displayName || "当前 Node"}</Text>
            <Text style={styles.meta}>这里配置实际在这台电脑上运行的 Agent、模型、MCP、Skill 和 Tool。</Text>
          </View>
          <AppPressable accessibilityLabel="刷新" onPress={() => void load()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable>
        </View>
        {loading ? <ActivityIndicator color={colors.accent} /> : null}

        <View style={styles.card}>
          <ResourceRow icon="agent" title="模型" detail={`${Object.keys(document?.models ?? {}).length} 个模型 · ${sharedModels} 个已共享`} onPress={() => router.push("/settings/models")} />
          <ResourceRow icon="agent" title="Agent" detail={`${enabledAgents.length} 个启用 · 默认 ${document?.agents.default_agent || "—"}`} onPress={() => router.push("/settings/agents")} />
          <ResourceRow icon="share" title="MCP 与 Skill" detail={`${Object.keys(document?.mcp_servers ?? {}).length} 个 MCP · ${Object.keys(document?.skills ?? {}).length} 个 Skill`} onPress={() => router.push("/settings/extensions")} />
          <ResourceRow icon="settings" title="Tool" detail={`${toolCount} 个当前会话可用 Tool · 权限由 Agent 和策略决定`} />
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Agent</Text>
          {agents.map(([id, agent]) => (
            <View key={id} style={styles.agentRow}>
              <View style={styles.flex}>
                <Text style={styles.rowTitle}>{agent.display_name}</Text>
                <Text style={styles.meta}>{agent.kind === "codex" ? "Codex Agent" : "Knoa Agent"} · 模型 {agent.model_binding.model || "由 Runtime 决定"}</Text>
              </View>
              <Text style={agent.enabled ? styles.enabled : styles.disabled}>{agent.enabled ? "启用" : "停用"}</Text>
            </View>
          ))}
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </>
  );
}

function ResourceRow({ icon, title, detail, onPress }: { icon: AppIconName; title: string; detail: string; onPress?: () => void }) {
  const content = <><AppIcon name={icon} color={colors.accent} size={22} /><View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>{onPress ? <AppIcon name="chevron-right" color={colors.muted} size={18} /> : null}</>;
  return onPress ? <AppPressable style={styles.row} onPress={onPress}>{content}</AppPressable> : <View style={styles.row}>{content}</View>;
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 },
  hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  heroIcon: { width: 50, height: 50, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  title: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  flex: { flex: 1, minWidth: 0 },
  card: { paddingHorizontal: 15, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800", marginTop: 13, marginBottom: 3 },
  row: { minHeight: 66, flexDirection: "row", alignItems: "center", gap: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  agentRow: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  enabled: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  disabled: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  error: { color: colors.danger, lineHeight: 20 },
});
