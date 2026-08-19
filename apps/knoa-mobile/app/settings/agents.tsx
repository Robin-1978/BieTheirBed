import { router, Stack } from "expo-router";
import { useCallback, useEffect, useState } from "react";
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
  const load = useCallback(async () => { try { const current = await gateway.runAuthenticated((client) => client.getConfigCurrent()); setDocument(current.revision.document); } catch (error) { setMessage(error instanceof Error ? error.message : "Agent 加载失败"); } }, [gateway.runAuthenticated]);
  useEffect(() => { void load(); }, [load]);

  async function publish(next: ManagedConfig, summary: string) {
    setWorking(summary); setMessage("");
    try {
      const created = await gateway.runAuthenticated((client) => client.createConfigDraft());
      const replaced = await gateway.runAuthenticated((client) => client.replaceConfigDraft(created.draft_id, next, created.draft_version));
      const validation = await gateway.runAuthenticated((client) => client.validateConfigDraft(replaced.draft_id, true));
      if (!validation.valid) throw new Error(validation.issues[0]?.message || "配置检查失败");
      const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(replaced.draft_id, replaced.draft_version, summary));
      if (result.state.apply_status === "failed") throw new Error(result.state.apply_error_code || "配置应用失败");
      setDocument(result.revision.document); setMessage("Agent 配置已生效");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Agent 配置失败"); }
    finally { setWorking(""); }
  }

  function setEnabled(agentId: string, enabled: boolean) { if (!document) return; const next = cloneManagedConfig(document); const target = next.agents.agents[agentId]; if (!target) return; target.enabled = enabled; void publish(next, `${enabled ? "启用" : "停用"} Agent`); }
  function setDefault(agentId: string) { if (!document) return; const next = cloneManagedConfig(document); const target = next.agents.agents[agentId]; if (!target) return; next.agents.default_agent = agentId; target.enabled = true; void publish(next, "设置默认 Agent"); }
  function bindModel(agentId: string, model: string) { if (!document) return; const next = cloneManagedConfig(document); const target = next.agents.agents[agentId]; if (!target) return; target.model_binding = { ...target.model_binding, ownership: "platform", model }; void publish(next, "更新 Agent 模型绑定"); }

  return <><Stack.Screen options={{ title: "Agent" }} /><ScrollView contentContainerStyle={styles.container}><View style={styles.hero}><View style={styles.icon}><AppIcon name="agent" color={colors.accent} size={27} /></View><View style={styles.flex}><Text style={styles.title}>Node Agent</Text><Text style={styles.meta}>Agent 是当前 Node 的工作角色。模型、Skill 和 Tool 是它执行时使用的能力。</Text></View></View>{!document ? <ActivityIndicator color={colors.accent} /> : Object.entries(document.agents.agents).map(([id, agent]) => <View key={id} style={styles.card}><View style={styles.row}><View style={styles.flex}><Text style={styles.cardTitle}>{agent.display_name}</Text><Text style={styles.meta}>{agent.kind === "codex" ? "Codex Agent · 模型由 Codex Runtime 管理" : "Knoa Agent"}{document.agents.default_agent === id ? " · 默认" : ""}</Text></View><Switch disabled={Boolean(working) || document.agents.default_agent === id} value={agent.enabled} onValueChange={(enabled) => setEnabled(id, enabled)} /></View>{agent.kind === "knoa" ? <><Text style={styles.label}>使用模型</Text><View style={styles.choices}>{Object.entries(document.models).map(([alias, model]) => { const provider = document.providers[model.provider]; const remote = provider?.driver === "workspace_remote"; return <AppPressable key={alias} disabled={Boolean(working) || agent.model_binding.ownership === "runtime"} style={[styles.choice, agent.model_binding.model === alias && styles.choiceSelected]} onPress={() => bindModel(id, alias)}><Text style={agent.model_binding.model === alias ? styles.choiceTextSelected : styles.choiceText}>{model.model || alias}{remote ? " · Workspace" : ""}</Text></AppPressable>; })}</View></> : null}{document.agents.default_agent !== id ? <AppPressable disabled={Boolean(working)} style={styles.secondary} onPress={() => setDefault(id)}><Text style={styles.secondaryText}>设为默认 Agent</Text></AppPressable> : null}</View>)}<AppPressable style={styles.advanced} onPress={() => router.push("/settings/system")}><Text style={styles.advancedText}>高级 Prompt、Policy 与运行限制</Text><AppIcon name="chevron-right" color={colors.muted} size={18} /></AppPressable>{working ? <ActivityIndicator color={colors.accent} /> : null}{message ? <Text style={styles.message}>{message}</Text> : null}</ScrollView></>;
}

const styles = StyleSheet.create({ container: { padding: 17, gap: 13, paddingBottom: 52 }, hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, icon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 19, fontWeight: "800" }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, card: { padding: 15, gap: 12, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, row: { flexDirection: "row", alignItems: "center", gap: 12 }, cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" }, label: { color: colors.ink, fontWeight: "700" }, choices: { flexDirection: "row", flexWrap: "wrap", gap: 7 }, choice: { paddingHorizontal: 11, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: colors.line }, choiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft }, choiceText: { color: colors.muted, fontSize: 12, fontWeight: "700" }, choiceTextSelected: { color: colors.accent, fontSize: 12, fontWeight: "800" }, secondary: { minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: colors.accent }, secondaryText: { color: colors.accent, fontWeight: "800" }, advanced: { minHeight: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 15, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, advancedText: { color: colors.muted, fontWeight: "700" }, message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13 } });
