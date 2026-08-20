import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import type { ManagedConfig } from "@/api/models";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function ExtensionCenterScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [kind, setKind] = useState<"skill" | "local_mcp" | "remote_mcp">("remote_mcp");
  const [source, setSource] = useState("");
  const [serverId, setServerId] = useState("");
  const [allowPrivate, setAllowPrivate] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [document, setDocument] = useState<ManagedConfig | null>(null);

  const load = useCallback(async () => {
    try {
      const current = await gateway.runAuthenticated((client) => client.getConfigCurrent());
      setDocument(current.revision.document);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.extensions.loadFailed"));
    }
  }, [gateway.runAuthenticated, t]);

  useEffect(() => { void load(); }, [load]);

  async function inspectAndCreateDraft() {
    if (!source.trim() || (kind !== "skill" && !serverId.trim())) return;
    setWorking(true);
    setMessage("");
    try {
      const result = await gateway.runAuthenticated((client) => {
        if (kind === "skill") return client.importSkill(source.trim());
        if (kind === "local_mcp") return client.importLocalMcp(source.trim(), serverId.trim());
        return client.importRemoteMcp(serverId.trim(), source.trim(), allowPrivate);
      });
      router.push({ pathname: "/settings/system", params: { draftId: result.draft.draft_id } });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.extensions.importFailed"));
    } finally {
      setWorking(false);
    }
  }

  const kindLabels = {
    remote_mcp: t("settings.extensions.remoteMcp"),
    local_mcp: t("settings.extensions.localMcp"),
    skill: t("settings.extensions.skillContent"),
  } as const;

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.section}>
        <Text style={styles.title}>{t("settings.extensions.currentNode")}</Text>
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
        <Text style={styles.title}>{t("settings.extensions.addTitle")}</Text>
        <Text style={styles.hint}>{t("settings.extensions.addHint")}</Text>
        <View style={styles.choices}>
          {(["remote_mcp", "local_mcp", "skill"] as const).map((value) => (
            <AppPressable key={value} style={[styles.choice, kind === value && styles.selected]} onPress={() => setKind(value)}>
              <Text style={kind === value ? styles.selectedText : styles.choiceText}>{kindLabels[value]}</Text>
            </AppPressable>
          ))}
        </View>
        {kind !== "skill" ? (
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
  item: { minHeight: 58, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  itemTitle: { color: colors.ink, fontWeight: "800" },
  enabled: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  disabled: { color: colors.muted, fontSize: 12, fontWeight: "700" },
});
