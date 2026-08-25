import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, View } from "react-native";

import type { ManagedConfig } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { cloneManagedConfig } from "@/models/modelConfiguration";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, shadows, spacing, typography } from "@/theme";

export default function AgentsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      const current = await gateway.runAuthenticated((client) => client.getConfigCurrent());
      setDocument(current.revision.document);
    } catch (error) {
      setDocument(null);
      setMessage(error instanceof Error ? error.message : t("settings.agents.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [gateway.runAuthenticated, t]);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  async function publish(next: ManagedConfig, summary: string) {
    setWorking(summary);
    setMessage("");
    try {
      const created = await gateway.runAuthenticated((client) => client.createConfigDraft());
      const replaced = await gateway.runAuthenticated((client) => client.replaceConfigDraft(created.draft_id, next, created.draft_version));
      const validation = await gateway.runAuthenticated((client) => client.validateConfigDraft(replaced.draft_id, true));
      if (!validation.valid) throw new Error(validation.issues[0]?.message || t("settings.common.configValidationFailed"));
      const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(replaced.draft_id, replaced.draft_version, summary));
      if (result.state.apply_status === "failed") throw new Error(result.state.apply_error_code || t("settings.common.configApplyFailed"));
      setDocument(result.revision.document);
      setMessage(t("settings.agents.publishSuccess"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.agents.publishFailed"));
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
    void publish(next, enabled ? t("settings.agents.enableAgent", { agentId }) : t("settings.agents.disableAgent", { agentId }));
  }

  function setDefault(agentId: string) {
    if (!document) return;
    const next = cloneManagedConfig(document);
    const target = next.agents.agents[agentId];
    if (!target || target.visibility !== "user") return;
    next.agents.default_agent = agentId;
    target.enabled = true;
    void publish(next, t("settings.agents.setDefaultAgent", { agentId }));
  }

  function visibilityLabel(value: "user" | "delegate" | "system") {
    if (value === "delegate") return t("settings.agents.visibilityDelegate");
    if (value === "system") return t("settings.agents.visibilitySystem");
    return t("settings.agents.visibilityUser");
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.hero}>
        <View style={styles.icon}><AppIcon name="agent" color={colors.accent} size={27} /></View>
        <View style={styles.flex}>
          <Text style={styles.title}>{t("settings.agents.title")}</Text>
          <Text style={styles.meta}>{t("settings.agents.heroDetail")}</Text>
        </View>
      </View>

      {!loading && !document && message ? (
        <AsyncStateView state="error" message={message} retryLabel={t("common.refresh")} onRetry={() => void load()} />
      ) : null}
      {loading && !document ? <AsyncStateView state="loading" /> : null}
      {document ? Object.entries(document.agents.agents).map(([id, agent]) => {
        const isDefault = document.agents.default_agent === id;
        const targets = agent.delegation.targets.map((targetId) => document.agents.agents[targetId]?.display_name || targetId);
        const toolsLabel = agent.allowed_platform_tools.includes("*")
          ? t("settings.agents.toolsBroad")
          : t("settings.agents.toolsCount", { count: agent.allowed_platform_tools.length });
        const subagentDetail = agent.delegation.allowed
          ? `${targets.length ? targets.join("、") : t("settings.agents.subagentNoTargets")} · ${t("settings.agents.subagentDepth", { depth: agent.delegation.max_depth })}`
          : t("settings.agents.subagentOff");
        return (
          <View key={id} style={styles.card}>
            <View style={styles.row}>
              <View style={styles.flex}>
                <Text style={styles.cardTitle}>{agent.display_name}</Text>
                <Text style={styles.meta}>
                  {agent.kind === "codex" ? t("settings.agents.codexRuntime") : t("settings.agents.knoaRuntime")} · {visibilityLabel(agent.visibility)}{isDefault ? t("settings.agents.defaultBadge") : ""}
                </Text>
              </View>
              <Switch disabled={Boolean(working) || isDefault} value={agent.enabled} onValueChange={(enabled) => setEnabled(id, enabled)} />
            </View>

            {agent.kind === "knoa" ? (
              <Text style={styles.detail}>{t("settings.agents.modelLine", { model: document.models[agent.model_binding.model]?.model || agent.model_binding.model })}</Text>
            ) : <Text style={styles.detail}>{t("settings.agents.codexModelManaged")}</Text>}
            <Text style={styles.detail}>
              {t("settings.agents.skillsLine", {
                skills: agent.default_skill_refs.length ? agent.default_skill_refs.join("、") : t("settings.agents.skillsNone"),
                tools: toolsLabel,
              })}
            </Text>
            <Text style={agent.delegation.allowed ? styles.healthy : styles.detail}>
              {t("settings.agents.subagentLine", { detail: subagentDetail })}
            </Text>

            <View style={styles.actions}>
              <AppPressable style={styles.secondary} onPress={() => router.push({ pathname: "/settings/agent-editor", params: { agentId: id } })}>
                <AppIcon name="edit" color={colors.accent} size={18} /><Text style={styles.secondaryText}>{t("settings.agents.configure")}</Text>
              </AppPressable>
              {!isDefault && agent.visibility === "user" ? (
                <AppPressable disabled={Boolean(working)} style={styles.secondary} onPress={() => setDefault(id)}>
                  <Text style={styles.secondaryText}>{t("settings.agents.setDefault")}</Text>
                </AppPressable>
              ) : null}
            </View>
          </View>
        );
      }) : null}

      <AppPressable disabled={Boolean(working) || !document} style={styles.primary} onPress={() => router.push({ pathname: "/settings/agent-editor", params: { mode: "new" } })}>
        <AppIcon name="plus" color={colors.onAccent} size={20} /><Text style={styles.primaryText}>{t("settings.agents.createKnoaAgent")}</Text>
      </AppPressable>
      <AppPressable style={styles.advanced} onPress={() => router.push("/settings/system")}>
        <Text style={styles.advancedText}>{t("settings.agents.advancedLink")}</Text><AppIcon name="chevron-right" color={colors.muted} size={18} />
      </AppPressable>
      {working ? <ActivityIndicator color={colors.accent} /> : null}
      {message && document ? <Text style={styles.message}>{message}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 52 },
  hero: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  icon: { width: 48, height: 48, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, ...typography.small, lineHeight: 18 },
  card: { padding: spacing.medium, gap: spacing.small, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  detail: { color: colors.muted, ...typography.small, lineHeight: 18 },
  healthy: { color: colors.accent, ...typography.small, lineHeight: 18, fontWeight: "700" },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: spacing.small },
  secondary: { minHeight: 42, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small, paddingHorizontal: spacing.medium, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  primary: { minHeight: 50, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small, borderRadius: radii.medium, backgroundColor: colors.accent },
  primaryText: { color: colors.onAccent, fontWeight: "800" },
  advanced: { minHeight: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  advancedText: { color: colors.muted, fontWeight: "700" },
  message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: radii.medium, padding: spacing.medium },
});
