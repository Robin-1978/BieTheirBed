import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import type { ManagedConfig } from "@/api/models";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { CAPABILITY_SCENARIOS } from "@/capabilityScenarios";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { loadCapabilityCache, storeCapabilityCache } from "@/storage/capabilityCache";

export default function CapabilitiesScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [toolCount, setToolCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const capabilityScope = gateway.nodeId || "unselected";

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
      const nextToolCount = result.descriptors?.length ?? 0;
      setToolCount(nextToolCount);
      void storeCapabilityCache(capabilityScope, { document: current.revision.document, toolCount: nextToolCount });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("capabilities.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [capabilityScope, gateway.client, gateway.runAuthenticated, gateway.sessionHandle, t]);

  useEffect(() => {
    let active = true;
    void loadCapabilityCache(capabilityScope).then((cached) => {
      if (!active || !cached) return;
      setDocument(cached.document);
      setToolCount(cached.toolCount);
      setLoading(false);
    }).finally(() => {
      if (active) void load();
    });
    return () => { active = false; };
  }, [capabilityScope, load]);

  const agents = document ? Object.entries(document.agents.agents) : [];
  const enabledAgents = agents.filter(([, agent]) => agent.enabled);
  const sharedModels = document
    ? Object.values(document.model_deployments).filter((deployment) => deployment.share_enabled).length
    : 0;

  function openScenario(prompt: string) {
    router.push({ pathname: "/(tabs)", params: { prefill: prompt } });
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.hero}>
        <View style={styles.heroIcon}><AppIcon name="agent" color={colors.accent} size={28} /></View>
        <View style={styles.flex}>
          <Text style={styles.title}>{t("capabilities.heroTitle")}</Text>
        </View>
        <AppPressable accessibilityLabel={t("common.refresh")} onPress={() => void load()} style={styles.iconButton}>
          <AppIcon name="refresh" color={colors.muted} size={20} />
        </AppPressable>
      </View>

      {loading && !document ? <AsyncStateView state="loading" /> : null}
      {error && !loading && !document ? (
        <AsyncStateView state="error" message={error} retryLabel={t("common.refresh")} onRetry={() => void load()} />
      ) : null}

      <View style={styles.scenarioCard}>
        <View style={styles.scenarioGrid}>
          {CAPABILITY_SCENARIOS.map((scenario) => (
            <AppPressable
              key={scenario.id}
              style={styles.scenario}
              onPress={() => openScenario(t(scenario.promptKey))}
            >
              <View style={styles.scenarioIcon}>
                <AppIcon name={scenario.icon} color={colors.accent} size={20} />
              </View>
              <Text style={styles.scenarioTitle} numberOfLines={2}>{t(scenario.titleKey)}</Text>
              <Text style={styles.scenarioDetail} numberOfLines={2}>{t(scenario.detailKey)}</Text>
            </AppPressable>
          ))}
        </View>
      </View>

      <View style={styles.advancedCard}>
        <AppPressable
          style={styles.advancedToggle}
          onPress={() => setAdvancedOpen((value) => !value)}
          accessibilityLabel={advancedOpen ? t("capabilities.advancedHide") : t("capabilities.advancedShow")}
        >
          <Text style={styles.advancedTitle}>{t("capabilities.advancedTitle")}</Text>
          <Text style={styles.advancedHint}>{advancedOpen ? t("capabilities.advancedHide") : t("capabilities.advancedShow")}</Text>
          <AppIcon name={advancedOpen ? "chevron-down" : "chevron-right"} color={colors.muted} size={18} />
        </AppPressable>

        {advancedOpen ? (
          <View style={styles.advancedBody}>
            <ResourceRow
              icon="agent"
              title={t("nav.models")}
              detail={t("capabilities.modelsDetail", {
                count: Object.keys(document?.models ?? {}).length,
                shared: sharedModels,
              })}
              onPress={() => router.push("/settings/models")}
            />
            <ResourceRow
              icon="agent"
              title={t("nav.agents")}
              detail={t("capabilities.agentsDetail", {
                enabled: enabledAgents.length,
                defaultAgent: document?.agents.default_agent || "—",
              })}
              onPress={() => router.push("/settings/agents")}
            />
            <ResourceRow
              icon="share"
              title={t("nav.extensions")}
              detail={t("capabilities.extensionsDetail", {
                mcp: Object.keys(document?.mcp_servers ?? {}).length,
                skills: Object.keys(document?.skills ?? {}).length,
              })}
              onPress={() => router.push("/settings/extensions")}
            />
            <ResourceRow
              icon="settings"
              title="Tool"
              detail={t("capabilities.toolsDetail", { count: toolCount })}
            />

            <Text style={styles.sectionTitle}>{t("capabilities.agentSection")}</Text>
            {agents.length ? agents.map(([id, agent]) => (
              <View key={id} style={styles.agentRow}>
                <View style={styles.flex}>
                  <Text style={styles.rowTitle}>{agent.display_name}</Text>
                  <Text style={styles.meta}>
                    {agent.kind === "codex" ? t("capabilities.codexAgent") : t("capabilities.knoaAgent")} · {t("capabilities.modelBinding", {
                      model: agent.model_binding.model || t("capabilities.modelRuntimeDecided"),
                    })}
                  </Text>
                </View>
                <Text style={agent.enabled ? styles.enabled : styles.disabled}>
                  {agent.enabled ? t("capabilities.enabled") : t("capabilities.disabled")}
                </Text>
              </View>
            )) : (
              <Text style={styles.meta}>{t("capabilities.loadFailed")}</Text>
            )}
          </View>
        ) : null}
      </View>

      {error && document ? <Text style={styles.error}>{error}</Text> : null}
    </ScrollView>
  );
}

function ResourceRow({ icon, title, detail, onPress }: { icon: AppIconName; title: string; detail: string; onPress?: () => void }) {
  const content = (
    <>
      <AppIcon name={icon} color={colors.accent} size={22} />
      <View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>
      {onPress ? <AppIcon name="chevron-right" color={colors.muted} size={18} /> : null}
    </>
  );
  return onPress ? <AppPressable style={styles.row} onPress={onPress}>{content}</AppPressable> : <View style={styles.row}>{content}</View>;
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 52 },
  hero: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  heroIcon: { width: 50, height: 50, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  title: { color: colors.ink, ...typography.heading },
  meta: { color: colors.muted, ...typography.small, lineHeight: 18 },
  flex: { flex: 1, minWidth: 0 },
  scenarioCard: { padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  scenarioGrid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.small },
  scenario: { width: "48%", minHeight: 112, padding: spacing.medium, borderRadius: radii.medium, backgroundColor: colors.background, gap: spacing.xsmall },
  scenarioIcon: { width: 36, height: 36, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  scenarioTitle: { color: colors.ink, ...typography.small, fontWeight: "800" },
  scenarioDetail: { color: colors.muted, fontSize: 11, lineHeight: 15 },
  advancedCard: { borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, overflow: "hidden", ...shadows.card },
  advancedToggle: { minHeight: 58, flexDirection: "row", alignItems: "center", gap: spacing.small, paddingHorizontal: spacing.large, paddingVertical: spacing.medium },
  advancedTitle: { color: colors.ink, fontWeight: "800", fontSize: 16 },
  advancedHint: { flex: 1, color: colors.muted, ...typography.small, textAlign: "right" },
  advancedBody: { paddingHorizontal: spacing.large, paddingBottom: spacing.medium, gap: spacing.xsmall },
  sectionTitle: { color: colors.ink, ...typography.subheading, fontWeight: "800", marginTop: spacing.medium, marginBottom: spacing.xsmall },
  row: { minHeight: 66, flexDirection: "row", alignItems: "center", gap: spacing.medium, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  agentRow: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: spacing.medium, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  enabled: { color: colors.accent, ...typography.small, fontWeight: "800" },
  disabled: { color: colors.muted, ...typography.small, fontWeight: "700" },
  error: { color: colors.danger, lineHeight: 20 },
});
