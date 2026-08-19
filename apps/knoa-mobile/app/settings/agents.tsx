import { router, Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, View } from "react-native";

import type { ManagedConfig } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { cloneManagedConfig } from "@/models/modelConfiguration";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function AgentsScreen() {
  const gateway = useGateway();
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const current = await gateway.runAuthenticated((client) => client.getConfigCurrent());
      setDocument(current.revision.document);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Agent 加载失败");
    }
  }, [gateway.runAuthenticated]);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  async function publish(next: ManagedConfig, summary: string) {
    setWorking(summary);
    setMessage("");
    try {
      const created = await gateway.runAuthenticated((client) => client.createConfigDraft());
      const replaced = await gateway.runAuthenticated((client) => client.replaceConfigDraft(created.draft_id, next, created.draft_version));
      const validation = await gateway.runAuthenticated((client) => client.validateConfigDraft(replaced.draft_id, true));
      if (!validation.valid) throw new Error(validation.issues[0]?.message || "配置检查失败");
      const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(replaced.draft_id, replaced.draft_version, summary));
      if (result.state.apply_status === "failed") throw new Error(result.state.apply_error_code || "配置应用失败");
      setDocument(result.revision.document);
      setMessage("Agent 配置已生效；运行中的 Invocation 保持原快照");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Agent 配置失败");
    } finally {
      setWorking("");
    }
  }

  function setEnabled(agentId: string, enabled: boolean) {
    if (!document) return;
    const next = cloneManagedConfig(document);
    const target = next.agents.agents[agentId];
    if (!target) return;
    target.enabled = enabled;
    void publish(next, `${enabled ? "启用" : "停用"} Agent ${agentId}`);
  }

  function setDefault(agentId: string) {
    if (!document) return;
    const next = cloneManagedConfig(document);
    const target = next.agents.agents[agentId];
    if (!target || target.visibility !== "user") return;
    next.agents.default_agent = agentId;
    target.enabled = true;
    void publish(next, `设置默认 Agent ${agentId}`);
  }

  return (
    <>
      <Stack.Screen options={{ title: "Agent" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.hero}>
          <View style={styles.icon}><AppIcon name="agent" color={colors.accent} size={27} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>当前 Node 的 Agent</Text>
            <Text style={styles.meta}>Agent 在当前 Node 执行。自定义工作角色复用 Knoa Runtime；Codex 使用内置 Adapter。这里不再拆分 Profile、Definition 和 Deployment。</Text>
          </View>
        </View>

        {!document ? <ActivityIndicator color={colors.accent} /> : Object.entries(document.agents.agents).map(([id, agent]) => {
          const isDefault = document.agents.default_agent === id;
          const targets = agent.delegation.targets.map((targetId) => document.agents.agents[targetId]?.display_name || targetId);
          return (
            <View key={id} style={styles.card}>
              <View style={styles.row}>
                <View style={styles.flex}>
                  <Text style={styles.cardTitle}>{agent.display_name}</Text>
                  <Text style={styles.meta}>{agent.kind === "codex" ? "Codex Runtime" : "Knoa Runtime"} · {visibilityLabel(agent.visibility)}{isDefault ? " · 默认" : ""}</Text>
                </View>
                <Switch disabled={Boolean(working) || isDefault} value={agent.enabled} onValueChange={(enabled) => setEnabled(id, enabled)} />
              </View>

              {agent.kind === "knoa" ? (
                <Text style={styles.detail}>模型：{document.models[agent.model_binding.model]?.model || agent.model_binding.model}</Text>
              ) : <Text style={styles.detail}>模型与 Thread 由 Codex Runtime 管理</Text>}
              <Text style={styles.detail}>Skill：{agent.default_skill_refs.length ? agent.default_skill_refs.join("、") : "未默认注入"} · Tool：{agent.allowed_platform_tools.includes("*") ? "由 Node/Task 权限收窄" : `${agent.allowed_platform_tools.length} 个允许项`}</Text>
              <Text style={agent.delegation.allowed ? styles.healthy : styles.detail}>Subagent：{agent.delegation.allowed ? `${targets.length ? targets.join("、") : "尚未选择目标"} · 深度 ${agent.delegation.max_depth}` : "关闭"}</Text>

              <View style={styles.actions}>
                <AppPressable style={styles.secondary} onPress={() => router.push({ pathname: "/settings/agent-editor", params: { agentId: id } })}>
                  <AppIcon name="edit" color={colors.accent} size={18} /><Text style={styles.secondaryText}>配置</Text>
                </AppPressable>
                {!isDefault && agent.visibility === "user" ? (
                  <AppPressable disabled={Boolean(working)} style={styles.secondary} onPress={() => setDefault(id)}><Text style={styles.secondaryText}>设为默认</Text></AppPressable>
                ) : null}
              </View>
            </View>
          );
        })}

        <AppPressable disabled={Boolean(working) || !document} style={styles.primary} onPress={() => router.push({ pathname: "/settings/agent-editor", params: { mode: "new" } })}>
          <AppIcon name="plus" color={colors.white} size={20} /><Text style={styles.primaryText}>新建 Knoa Agent</Text>
        </AppPressable>
        <AppPressable style={styles.advanced} onPress={() => router.push("/settings/system")}>
          <Text style={styles.advancedText}>配置发布状态与 Node 级运行参数</Text><AppIcon name="chevron-right" color={colors.muted} size={18} />
        </AppPressable>
        {working ? <ActivityIndicator color={colors.accent} /> : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </ScrollView>
    </>
  );
}

function visibilityLabel(value: "user" | "delegate" | "system") {
  if (value === "delegate") return "Subagent";
  if (value === "system") return "系统专用";
  return "用户可选";
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 },
  hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  icon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  card: { padding: 15, gap: 10, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  detail: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  healthy: { color: colors.accent, fontSize: 12, lineHeight: 18, fontWeight: "700" },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  secondary: { minHeight: 42, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingHorizontal: 13, borderRadius: 12, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  primary: { minHeight: 50, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 14, backgroundColor: colors.accent },
  primaryText: { color: colors.white, fontWeight: "800" },
  advanced: { minHeight: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 15, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  advancedText: { color: colors.muted, fontWeight: "700" },
  message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13 },
});
