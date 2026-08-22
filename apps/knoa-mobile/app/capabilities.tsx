import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import type { ManagedConfig } from "@/api/models";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { loadCapabilityCache, storeCapabilityCache } from "@/storage/capabilityCache";
import { TASK_TEMPLATES } from "@/taskTemplates";

export default function NodeResourcesScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [toolCount, setToolCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
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
      const toolCount = result.descriptors?.length ?? 0;
      setToolCount(toolCount);
      void storeCapabilityCache(capabilityScope, { document: current.revision.document, toolCount });
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

  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  const agents = document ? Object.entries(document.agents.agents) : [];
  const enabledAgents = agents.filter(([, agent]) => agent.enabled);
  const sharedModels = document
    ? Object.values(document.model_deployments).filter((deployment) => deployment.share_enabled).length
    : 0;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.hero}>
        <View style={styles.heroIcon}><AppIcon name="node" color={colors.accent} size={28} /></View>
        <View style={styles.flex}>
          <Text style={styles.title}>{node?.displayName || t("capabilities.currentNode")}</Text>
          <Text style={styles.meta}>{t("capabilities.heroDetail")}</Text>
        </View>
        <AppPressable accessibilityLabel={t("common.refresh")} onPress={() => void load()} style={styles.iconButton}>
          <AppIcon name="refresh" color={colors.muted} size={20} />
        </AppPressable>
      </View>
      {loading ? <ActivityIndicator color={colors.accent} /> : null}

      <View style={styles.capabilityCard}>
        <Text style={styles.sectionTitle}>{t("workspace.capabilitiesTitle")}</Text>
        <Text style={styles.meta}>{t("workspace.capabilitiesDetail")}</Text>
        <View style={styles.capabilityGrid}>
          {TASK_TEMPLATES.map((template) => (
            <AppPressable
              key={template.id}
              style={styles.capability}
              onPress={() => router.push({ pathname: "/tasks/new", params: { template: template.id } })}
            >
              <AppIcon name={template.id === "computer-health" || template.id === "service-monitor" ? "settings" : template.id === "project-maintenance" ? "agent" : "file"} color={colors.accent} size={19} />
              <View style={styles.flex}>
                <Text style={styles.capabilityTitle} numberOfLines={2}>{t(template.titleKey)}</Text>
                <Text style={styles.capabilityDetail} numberOfLines={2}>{t(template.detailKey)}</Text>
              </View>
            </AppPressable>
          ))}
        </View>
      </View>

      <View style={styles.card}>
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
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>{t("capabilities.agentSection")}</Text>
        {agents.map(([id, agent]) => (
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
        ))}
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
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
  container: { padding: 17, gap: 13, paddingBottom: 52 },
  hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  heroIcon: { width: 50, height: 50, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  title: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  flex: { flex: 1, minWidth: 0 },
  card: { paddingHorizontal: 15, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  capabilityCard: { padding: 15, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, gap: 6 },
  capabilityGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 4 },
  capability: { width: "48%", minHeight: 76, flexDirection: "row", alignItems: "center", gap: 8, padding: 10, borderRadius: 12, backgroundColor: colors.background },
  capabilityTitle: { color: colors.ink, fontSize: 12, fontWeight: "800" },
  capabilityDetail: { color: colors.muted, fontSize: 10, lineHeight: 14, marginTop: 2 },
  sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800", marginTop: 13, marginBottom: 3 },
  row: { minHeight: 66, flexDirection: "row", alignItems: "center", gap: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  agentRow: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  enabled: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  disabled: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  error: { color: colors.danger, lineHeight: 20 },
});
