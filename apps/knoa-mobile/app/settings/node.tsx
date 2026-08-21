import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportDetailKey, transportLabelKey } from "@/api/transportPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type RuntimeDiagnostic = {
  modelCalls: unknown;
  toolCalls: unknown;
  totalTokens: unknown;
};

export default function NodeSettingsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [diagnostic, setDiagnostic] = useState<RuntimeDiagnostic | null>(null);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!gateway.sessionHandle) return;
    try {
      const runtime = await gateway.runAuthenticated((client) => client.runtimeStatus(gateway.sessionHandle));
      const details = (runtime.result as { details?: Record<string, unknown> }).details ?? {};
      setDiagnostic({ modelCalls: details.model_calls ?? 0, toolCalls: details.tool_calls ?? 0, totalTokens: details.total_tokens ?? 0 });
    } catch {
      setDiagnostic(null);
    }
  }, [gateway.runAuthenticated, gateway.sessionHandle]);

  useEffect(() => { void load(); }, [load]);

  async function run(name: string, operation: () => Promise<void>, success: string) {
    setWorking(name);
    setMessage("");
    try {
      await operation();
      setMessage(success);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.common.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  function removeBinding() {
    Alert.alert(t("settings.node.removeBindingTitle"), t("settings.node.removeBindingMessage"), [
      { text: t("common.cancel"), style: "cancel" },
      {
        text: t("settings.node.removeBindingConfirm"),
        style: "destructive",
        onPress: () => void run("remove", async () => {
          await gateway.removeConnection();
          router.replace("/account");
        }, t("settings.node.removeBindingSuccess")),
      },
    ]);
  }

  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.card}>
        <View style={styles.header}>
          <View style={styles.icon}><AppIcon name="node" color={colors.accent} size={25} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{node?.displayName || t("nav.node")}</Text>
            <Text style={gateway.status === "ready" ? styles.online : styles.offline}>
              {gateway.status === "ready" ? t("nodeSettings.connected") : t("nodeHeader.connecting")}
            </Text>
          </View>
        </View>
        {gateway.status === "ready" ? <Text style={styles.transportDetail}>{t("nodeSettings.autoTransportDetail")}</Text> : null}
        {gateway.status === "ready" ? <Metric label={t("nodeSettings.activeTransport")} value={t(transportLabelKey(gateway.transportMode))} /> : null}
        {gateway.status === "ready" ? <Text style={styles.transportDetail}>{transportDetailKey(gateway.transportMode) ? `${t("nodeSettings.transportDiagnostic")} · ${t(transportDetailKey(gateway.transportMode))}` : ""}</Text> : null}
        {gateway.status === "ready" ? <Metric label={t("settings.node.p2pState")} value={p2pStateLabel(gateway.p2pState, t)} /> : null}
        {gateway.p2pLastError ? <Text selectable style={styles.p2pError}>{t("settings.node.p2pLastError", { error: gateway.p2pLastError })}</Text> : null}
        <Metric label={t("settings.node.mdnsState")} value={mdnsStateLabel(gateway.lanState, t)} />
        {gateway.lanState === "found" && gateway.lanEndpoint ? <Text selectable style={styles.lanEndpoint}>{t("settings.node.mdnsFoundEndpoint", { endpoint: gateway.lanEndpoint })}</Text> : null}
        {gateway.lanLastError ? <Text selectable style={styles.p2pError}>{t("settings.node.mdnsLastError", { error: gateway.lanLastError })}</Text> : null}
        <Metric label={t("common.gateway")} value={gateway.gatewayUrl || "—"} />
        <Metric label={t("common.nodeId")} value={gateway.nodeId || "—"} compact />
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>{t("nodeMenu.connection")}</Text>
        <Action title={t("settings.node.reconnectTitle")} detail={t("settings.node.reconnectDetail")} busy={working === "reconnect"} onPress={() => void run("reconnect", gateway.reconnect, t("settings.node.reconnectSuccess"))} />
        <Action title={t("settings.node.reauthTitle")} detail={t("settings.node.reauthDetail")} busy={working === "reauth"} onPress={() => void run("reauth", gateway.reauthenticate, t("settings.node.reauthSuccess"))} />
        <Action title={t("settings.node.repairPairingTitle")} detail={t("settings.node.repairPairingDetail")} onPress={() => router.push("/pair")} />
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>{t("settings.node.runtimeOverview")}</Text>
        {diagnostic ? (
          <>
            <Metric label={t("settings.node.modelCalls")} value={diagnostic.modelCalls} />
            <Metric label={t("settings.node.toolCalls")} value={diagnostic.toolCalls} />
            <Metric label={t("settings.node.tokens")} value={diagnostic.totalTokens} />
          </>
        ) : <Text style={styles.meta}>{t("settings.node.runtimeUnavailable")}</Text>}
        <Action title={t("settings.node.advancedSystem")} detail={t("settings.node.advancedSystemDetail")} onPress={() => router.push("/settings/system")} />
      </View>

      <AppPressable disabled={Boolean(working)} style={styles.remove} onPress={removeBinding}>
        {working === "remove" ? <ActivityIndicator color={colors.danger} /> : <Text style={styles.removeText}>{t("settings.node.removeBinding")}</Text>}
      </AppPressable>
      {message ? <Text style={styles.message}>{message}</Text> : null}
    </ScrollView>
  );
}

function Metric({ label, value, compact = false }: { label: string; value: unknown; compact?: boolean }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.meta}>{label}</Text>
      <Text numberOfLines={compact ? 1 : 2} style={[styles.metricValue, compact && styles.compact]}>{String(value)}</Text>
    </View>
  );
}

function p2pStateLabel(state: "idle" | "connecting" | "ready" | "active" | "cooldown", t: ReturnType<typeof useI18n>["t"]) {
  if (state === "connecting") return t("settings.node.p2pConnecting");
  if (state === "ready") return t("settings.node.p2pReady");
  if (state === "active") return t("settings.node.p2pActive");
  if (state === "cooldown") return t("settings.node.p2pCooldown");
  return t("settings.node.p2pIdle");
}

function mdnsStateLabel(state: "idle" | "scanning" | "found" | "cooldown", t: ReturnType<typeof useI18n>["t"]) {
  if (state === "scanning") return t("settings.node.mdnsScanning");
  if (state === "found") return t("settings.node.mdnsFound");
  if (state === "cooldown") return t("settings.node.mdnsCooldown");
  return t("settings.node.mdnsIdle");
}

function Action({ title, detail, busy = false, onPress }: { title: string; detail: string; busy?: boolean; onPress(): void }) {
  return (
    <AppPressable disabled={busy} style={styles.action} onPress={onPress}>
      <View style={styles.flex}><Text style={styles.actionTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>
      {busy ? <ActivityIndicator color={colors.accent} /> : <AppIcon name="chevron-right" color={colors.muted} size={18} />}
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 },
  card: { padding: 16, gap: 10, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  header: { flexDirection: "row", alignItems: "center", gap: 12 },
  icon: { width: 48, height: 48, alignItems: "center", justifyContent: "center", borderRadius: 15, backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  online: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  offline: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800" },
  metric: { flexDirection: "row", justifyContent: "space-between", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, paddingTop: 10 },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  transportDetail: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  p2pError: { color: colors.danger, fontSize: 11, lineHeight: 17, padding: 10, borderRadius: 10, backgroundColor: colors.background },
  lanEndpoint: { color: colors.accent, fontSize: 11, lineHeight: 17 },
  metricValue: { flex: 1, color: colors.ink, fontWeight: "700", textAlign: "right" },
  compact: { fontFamily: "monospace", fontSize: 11 },
  action: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 11, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  actionTitle: { color: colors.ink, fontWeight: "800" },
  remove: { minHeight: 48, alignItems: "center", justifyContent: "center" },
  removeText: { color: colors.danger, fontWeight: "800" },
  message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13 },
});
