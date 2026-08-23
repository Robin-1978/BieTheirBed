import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import type { CapabilityCatalogEntry, CapabilityInstallPlan, CapabilityInstallation, ExtensionImportResult, ManagedConfig } from "@/api/models";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { BUSINESS_CONNECTIONS, connectionDescriptor, type BusinessConnectionKind } from "@/models/connectionWizard";

export default function ExtensionCenterScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [kind, setKind] = useState<"capability" | "skill" | "local_mcp" | "remote_mcp">("capability");
  const [connectionKind, setConnectionKind] = useState<BusinessConnectionKind>("custom");
  const [source, setSource] = useState("");
  const [serverId, setServerId] = useState("");
  const [allowPrivate, setAllowPrivate] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [inspection, setInspection] = useState<ExtensionImportResult["inspection"] | null>(null);
  const [draftId, setDraftId] = useState("");
  const [installations, setInstallations] = useState<CapabilityInstallation[]>([]);
  const [installPlan, setInstallPlan] = useState<CapabilityInstallPlan | null>(null);
  const [catalog, setCatalog] = useState<CapabilityCatalogEntry[]>([]);

  const load = useCallback(async () => {
    try {
      const [current, installed, entries] = await gateway.runAuthenticated((client) => Promise.all([
        client.getConfigCurrent(),
        client.listCapabilityInstallations(),
        client.listCapabilityCatalog(),
      ]));
      setDocument(current.revision.document);
      setInstallations(installed);
      setCatalog(entries);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.extensions.loadFailed"));
    }
  }, [gateway.runAuthenticated, t]);

  useEffect(() => { void load(); }, [load]);

  async function inspectAndCreateDraft() {
    if (!source.trim() || (kind !== "skill" && kind !== "capability" && !serverId.trim())) return;
    setWorking(true);
    setMessage("");
    try {
      if (kind === "capability") {
        const plan = await gateway.runAuthenticated(
          (client) => client.prepareCapability(source.trim()),
        );
        setInstallPlan(plan);
        setInspection(null);
        setMessage(t("settings.extensions.planReady"));
        return;
      }
      const imported = await gateway.runAuthenticated((client) => {
        if (kind === "skill") return client.importSkill(source.trim());
        if (kind === "local_mcp") return client.importLocalMcp(source.trim(), serverId.trim());
        return client.importRemoteMcp(serverId.trim(), source.trim(), allowPrivate);
      });
      setInspection(imported.inspection);
      setDraftId(imported.draft.draft_id);
      setMessage(t("settings.extensions.inspectSuccess"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.extensions.importFailed"));
    } finally {
      setWorking(false);
    }
  }

  const kindLabels = {
    capability: t("settings.extensions.capabilityBundle"),
    remote_mcp: t("settings.extensions.remoteMcp"),
    local_mcp: t("settings.extensions.localMcp"),
    skill: t("settings.extensions.skillContent"),
  } as const;

  async function confirmInstall() {
    if (!installPlan) return;
    setWorking(true);
    try {
      await gateway.runAuthenticated((client) => client.confirmCapability(installPlan));
      setInstallPlan(null);
      setMessage(t("settings.extensions.installSuccess"));
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.extensions.importFailed"));
    } finally {
      setWorking(false);
    }
  }

  async function capabilityAction(capabilityId: string, action: "toggle" | "rollback", enabled = false) {
    setWorking(true);
    try {
      await gateway.runAuthenticated((client) => action === "rollback"
        ? client.rollbackCapability(capabilityId)
        : client.setCapabilityEnabled(capabilityId, enabled));
      await load();
    } finally {
      setWorking(false);
    }
  }

  async function prepareCatalog(item: CapabilityCatalogEntry) {
    setWorking(true);
    setMessage("");
    try {
      setInstallPlan(await gateway.runAuthenticated((client) => client.prepareCatalogCapability(
        item.id, item.selection.mode, item.selection.version,
      )));
      setMessage(t("settings.extensions.planReady"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.extensions.importFailed"));
    } finally { setWorking(false); }
  }

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.section}>
        <Text style={styles.title}>{t("settings.extensions.currentNode")}</Text>
        {installations.map((item) => (
          <View key={`capability:${item.capability_id}`} style={styles.capabilityItem}>
            <View style={styles.flex}>
              <Text style={styles.itemTitle}>{item.display_name}</Text>
              <Text style={styles.hint}>v{item.version} · {item.health}</Text>
            </View>
            <View style={styles.choices}>
              <AppPressable style={styles.smallButton} disabled={working} onPress={() => void capabilityAction(item.capability_id, "toggle", !item.enabled)}>
                <Text style={styles.choiceText}>{item.enabled ? t("capabilities.disable") : t("capabilities.enable")}</Text>
              </AppPressable>
              <AppPressable style={styles.smallButton} disabled={working} onPress={() => void capabilityAction(item.capability_id, "rollback")}>
                <Text style={styles.choiceText}>{t("settings.extensions.rollback")}</Text>
              </AppPressable>
            </View>
          </View>
        ))}
        {Object.entries(document?.mcp_servers ?? {}).map(([id, server]) => (
          <View key={`mcp:${id}`} style={styles.item}>
            <View><Text style={styles.itemTitle}>{id}</Text><Text style={styles.hint}>{t("config.mcpDetail", { transport: server.transport })}</Text></View>
            <Text style={server.enabled ? styles.enabled : styles.disabled}>{server.enabled ? t("capabilities.enabled") : t("capabilities.disabled")}</Text>
          </View>
        ))}
        {Object.entries(document?.skills ?? {}).map(([id, skill]) => (
          <View key={`skill:${id}`} style={styles.item}>
            <View><Text style={styles.itemTitle}>{id}</Text><Text style={styles.hint}>{t("config.skillDetail", { source: skill.source || t("settings.extensions.installedContent") })}</Text></View>
            <Text style={skill.enabled ? styles.enabled : styles.disabled}>{skill.enabled ? t("capabilities.enabled") : t("capabilities.disabled")}</Text>
          </View>
        ))}
        {!Object.keys(document?.mcp_servers ?? {}).length && !Object.keys(document?.skills ?? {}).length
          ? <Text style={styles.hint}>{t("settings.extensions.empty")}</Text>
          : null}
      </View>
      <View style={styles.section}>
        <Text style={styles.title}>{t("settings.extensions.catalogTitle")}</Text>
        <Text style={styles.hint}>{t("settings.extensions.catalogHint")}</Text>
        {catalog.map((item) => (
          <View key={`${item.id}:${item.version}`} style={styles.capabilityItem}>
            <View style={styles.flex}>
              <Text style={styles.itemTitle}>{item.display_name} · v{item.version}</Text>
              <Text style={styles.hint}>{item.description}</Text>
              <Text style={styles.hint}>{item.permission_summary.join(" · ")}</Text>
              {item.revoked ? <Text style={styles.warning}>{t("settings.extensions.revoked")}</Text> : null}
            </View>
            <AppPressable style={styles.smallButton} disabled={working || item.revoked} onPress={() => void prepareCatalog(item)}>
              <Text style={styles.choiceText}>{t("settings.extensions.catalogInstall")}</Text>
            </AppPressable>
          </View>
        ))}
      </View>
      {installPlan ? (
        <View style={styles.section}>
          <Text style={styles.title}>{installPlan.display_name} · v{installPlan.version}</Text>
          <Text style={styles.hint}>{t("settings.extensions.permissionDelta", { count: installPlan.requested_tools.length })}</Text>
          {installPlan.requested_tools.map((tool) => (
            <Text key={tool.name} style={tool.risk === "high" ? styles.warning : styles.hint}>
              {tool.name} · {tool.effect} · {tool.risk}
            </Text>
          ))}
          {installPlan.withheld_tools.length ? <Text style={styles.warning}>{t("settings.extensions.inspectionWithheld", { items: installPlan.withheld_tools.join("、") })}</Text> : null}
          <Text style={styles.hint}>{t("settings.extensions.healthChecks", { count: installPlan.checks.length })}</Text>
          <AppPressable style={styles.primary} disabled={working} onPress={() => void confirmInstall()}>
            {working ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>{t("settings.extensions.confirmInstall")}</Text>}
          </AppPressable>
        </View>
      ) : null}
      {inspection ? (
        <View style={styles.section}>
          <Text style={styles.title}>{t("settings.extensions.inspectionTitle")}</Text>
          <Text style={styles.hint}>{t("settings.extensions.inspectionSummary", { id: inspection.extension_id })}</Text>
          <Text style={styles.hint}>{t("settings.extensions.inspectionTools", { count: inspection.tools.length })}</Text>
          <Text style={styles.hint}>{t("settings.extensions.inspectionResources", { count: inspection.resources.length })}</Text>
          <Text style={styles.hint}>{t("settings.extensions.inspectionPrompts", { count: inspection.prompts.length })}</Text>
          {inspection.requested_secrets.length ? <Text style={styles.warning}>{t("settings.extensions.inspectionSecrets", { items: inspection.requested_secrets.join("、") })}</Text> : null}
          {inspection.withheld_tools.length ? <Text style={styles.warning}>{t("settings.extensions.inspectionWithheld", { items: inspection.withheld_tools.join("、") })}</Text> : null}
          <AppPressable style={styles.primary} disabled={!draftId} onPress={() => router.push({ pathname: "/settings/system", params: { draftId } })}>
            <Text style={styles.primaryText}>{t("settings.extensions.openDraft")}</Text>
          </AppPressable>
        </View>
      ) : null}
      <View style={styles.section}>
        <Text style={styles.title}>{t("settings.extensions.addTitle")}</Text>
        <Text style={styles.hint}>{t("settings.extensions.addHint")}</Text>
        <Text style={styles.label}>{t("connections.title")}</Text>
        <View style={styles.choices}>
          {BUSINESS_CONNECTIONS.map((connection) => (
            <AppPressable key={connection.kind} style={[styles.choice, connectionKind === connection.kind && styles.selected]} onPress={() => { setConnectionKind(connection.kind); if (connection.defaultServerId) setServerId(connection.defaultServerId); }}>
              <Text style={connectionKind === connection.kind ? styles.selectedText : styles.choiceText}>{t(connection.titleKey as never)}</Text>
            </AppPressable>
          ))}
        </View>
        <Text style={styles.hint}>{t(connectionDescriptor(connectionKind).detailKey as never)}</Text>
        {connectionDescriptor(connectionKind).capabilities.length ? <Text style={styles.hint}>{t("connections.capabilities", { items: connectionDescriptor(connectionKind).capabilities.join("、") })}</Text> : null}
        <View style={styles.choices}>
          {(["capability", "remote_mcp", "local_mcp", "skill"] as const).map((value) => (
            <AppPressable key={value} style={[styles.choice, kind === value && styles.selected]} onPress={() => setKind(value)}>
              <Text style={kind === value ? styles.selectedText : styles.choiceText}>{kindLabels[value]}</Text>
            </AppPressable>
          ))}
        </View>
        {kind !== "skill" && kind !== "capability" ? (
          <TextInput value={serverId} onChangeText={setServerId} placeholder={t("settings.extensions.serverIdPlaceholder")} placeholderTextColor={colors.muted} style={styles.input} autoCapitalize="none" />
        ) : null}
        <TextInput
          value={source}
          onChangeText={setSource}
          placeholder={kind === "remote_mcp" ? t("settings.extensions.remoteUrlPlaceholder") : t("settings.extensions.localPathPlaceholder")}
          placeholderTextColor={colors.muted}
          style={styles.input}
          autoCapitalize="none"
        />
        {kind === "remote_mcp" ? (
          <View style={styles.row}><Text style={styles.label}>{t("settings.extensions.allowPrivateNetwork")}</Text><Switch value={allowPrivate} onValueChange={setAllowPrivate} /></View>
        ) : null}
        <AppPressable style={styles.primary} disabled={working} onPress={() => void inspectAndCreateDraft()}>
          {working ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>{t("settings.extensions.inspectAndDraft")}</Text>}
        </AppPressable>
        {message ? <Text style={styles.error}>{message}</Text> : null}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 18, gap: 16, backgroundColor: colors.background },
  section: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 18, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  choices: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  choice: { paddingHorizontal: 12, paddingVertical: 9, borderRadius: 12, backgroundColor: colors.background },
  selected: { backgroundColor: colors.accent },
  choiceText: { color: colors.ink, fontWeight: "700" },
  selectedText: { color: "#fff", fontWeight: "800" },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 12, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  label: { color: colors.ink, fontWeight: "600" },
  primary: { minHeight: 46, backgroundColor: colors.accent, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  primaryText: { color: "#fff", fontWeight: "800" },
  error: { color: colors.danger, fontSize: 13 },
  warning: { color: colors.warning, fontSize: 13, lineHeight: 19 },
  item: { minHeight: 58, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  capabilityItem: { gap: 10, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, paddingTop: 12 },
  flex: { flex: 1 },
  smallButton: { paddingHorizontal: 10, paddingVertical: 7, borderRadius: 10, backgroundColor: colors.background },
  itemTitle: { color: colors.ink, fontWeight: "800" },
  enabled: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  disabled: { color: colors.muted, fontSize: 12, fontWeight: "700" },
});
