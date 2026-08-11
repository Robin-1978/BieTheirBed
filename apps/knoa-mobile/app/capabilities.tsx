import * as Application from "expo-application";
import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { useI18n, type LanguageMode } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { useThemePreference, type ThemeMode } from "@/state/ThemeProvider";
import type { PushRegistrationStatus } from "@/notifications";
import { colors } from "@/theme";

type Translate = ReturnType<typeof useI18n>["t"];

type Extension = {
  extension_id: string;
  kind: "skill" | "mcp";
  state: string;
  detail: string;
  tools: string[];
};

type Descriptor = {
  name: string;
  origin_kind: string;
  effect: string;
  risk: string;
  requires_confirmation: boolean;
};

type Diagnostic = {
  status: Record<string, unknown> | null;
  extensions: Extension[];
  tools: Descriptor[];
  audit: Array<Record<string, unknown>>;
};

export default function CapabilitiesScreen() {
  const gateway = useGateway();
  const theme = useThemePreference();
  const i18n = useI18n();
  const [diagnostic, setDiagnostic] = useState<Diagnostic>({
    status: null,
    extensions: [],
    tools: [],
    audit: [],
  });
  const [notificationStatus, setNotificationStatus] = useState(i18n.t("settings.checking"));
  const [advanced, setAdvanced] = useState(false);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    const permission = await Notifications.getPermissionsAsync();
    setNotificationStatus(permission.status === "granted" ? i18n.t("settings.allowed") : i18n.t("settings.notAllowed"));
    if (!gateway.client || !gateway.sessionHandle) return;
    try {
      const [runtime, inventory, deviceAudit] = await gateway.runAuthenticated((client) => Promise.all([
        client.runtimeStatus(gateway.sessionHandle),
        client.tools(gateway.sessionHandle),
        client.deviceAudit(),
      ]));
      const runtimeResult = runtime.result as Record<string, unknown>;
      const inventoryResult = inventory.result as Record<string, unknown>;
      setDiagnostic({
        status: (runtimeResult.details as Record<string, unknown>) ?? {},
        extensions: (runtimeResult.extensions as Extension[]) ?? [],
        tools: (inventoryResult.descriptors as Descriptor[]) ?? [],
        audit: (deviceAudit.events as Array<Record<string, unknown>>) ?? [],
      });
    } catch {
      setMessage(i18n.t("settings.diagnosticUnavailable"));
    }
  }, [gateway.client, gateway.runAuthenticated, gateway.sessionHandle, i18n]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(name: string, action: () => Promise<void>, success: string) {
    if (working) return;
    setWorking(name);
    setMessage("");
    try {
      await action();
      setMessage(success);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : i18n.t("settings.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  function confirmRemoval() {
    Alert.alert(
      i18n.t("settings.removeTitle"),
      i18n.t("settings.removeBody"),
      [
        { text: i18n.t("settings.cancel"), style: "cancel" },
        {
          text: i18n.t("settings.removeDevice"),
          style: "destructive",
          onPress: () => void runAction("remove", async () => {
            await gateway.removeConnection();
            router.replace("/pair");
          }, i18n.t("settings.deviceRemoved")),
        },
      ],
    );
  }

  const serviceLabel = gateway.status === "ready"
    ? i18n.t("settings.service.ready")
    : gateway.status === "booting"
      ? i18n.t("settings.service.booting")
      : gateway.status === "error"
        ? i18n.t("settings.service.error")
        : i18n.t("settings.service.unpaired");
  const pushStatusLabel = pushRegistrationLabel(
    gateway.pushRegistration.status,
    i18n.t,
  );

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Section title={i18n.t("settings.appearance")}>
        <Text style={styles.themeHint}>{i18n.t("settings.appearanceHint")}</Text>
        <View accessibilityRole="radiogroup" style={styles.themeChoices}>
          <ThemeChoice label={i18n.t("settings.theme.system")} mode="system" selected={theme.mode === "system"} onPress={theme.setMode} />
          <ThemeChoice label={i18n.t("settings.theme.light")} mode="light" selected={theme.mode === "light"} onPress={theme.setMode} />
          <ThemeChoice label={i18n.t("settings.theme.dark")} mode="dark" selected={theme.mode === "dark"} onPress={theme.setMode} />
        </View>
      </Section>

      <Section title={i18n.t("settings.language")}>
        <Text style={styles.themeHint}>{i18n.t("settings.languageHint")}</Text>
        <View accessibilityRole="radiogroup" style={styles.themeChoices}>
          <LanguageChoice label={i18n.t("settings.language.system")} mode="system" selected={i18n.mode === "system"} onPress={i18n.setMode} />
          <LanguageChoice label={i18n.t("settings.language.zh")} mode="zh-CN" selected={i18n.mode === "zh-CN"} onPress={i18n.setMode} />
          <LanguageChoice label={i18n.t("settings.language.en")} mode="en-US" selected={i18n.mode === "en-US"} onPress={i18n.setMode} />
        </View>
      </Section>

      <Section title={i18n.t("settings.connectionStatus")}>
        <Metric label={i18n.t("settings.service")} value={serviceLabel} tone={gateway.status === "error" ? "danger" : "normal"} />
        <Metric label={i18n.t("settings.serviceAddress")} value={gateway.gatewayUrl || "—"} />
        <Metric label={i18n.t("settings.deviceId")} value={gateway.deviceId || "—"} compact />
        <Metric label={i18n.t("settings.lastConnected")} value={formatTime(gateway.lastConnectedAt, i18n.locale, i18n.t("settings.never"))} />
        {gateway.error ? <Text style={styles.error}>{i18n.t("settings.connectionProblem")}</Text> : null}
      </Section>

      <Section title={i18n.t("settings.deviceAndApp")}>
        <Metric label={i18n.t("settings.notificationPermission")} value={notificationStatus} />
        <Metric
          label={i18n.t("settings.pushRegistration")}
          value={pushStatusLabel}
          tone={gateway.pushRegistration.status === "server_failed" || gateway.pushRegistration.status === "token_failed" ? "danger" : "normal"}
        />
        <Metric label={i18n.t("settings.appVersion")} value={`${Application.nativeApplicationVersion ?? i18n.t("settings.development")} (${Application.nativeBuildVersion ?? "—"})`} />
        <Metric label={i18n.t("settings.localIdentity")} value={gateway.deviceId ? i18n.t("settings.secureStorage") : i18n.t("settings.none")} />
        <Action
          label={i18n.t("settings.enableNotifications")}
          detail={i18n.t("settings.enableNotificationsDetail")}
          busy={working === "notifications"}
          onPress={() => void runAction("notifications", async () => {
            if (!gateway.client) throw new Error(i18n.t("settings.connectFirst"));
            const result = await gateway.registerNotifications(true);
            if (result.status === "permission_denied") {
              const permission = await Notifications.getPermissionsAsync();
              if (!permission.canAskAgain) await Linking.openSettings();
            }
            if (result.status !== "registered") {
              throw new Error(pushRegistrationLabel(result.status, i18n.t));
            }
          }, i18n.t("settings.notificationsEnabled"))}
        />
        <Action
          label={i18n.t("settings.testNotification")}
          detail={i18n.t("settings.testNotificationDetail")}
          busy={working === "test-notification"}
          onPress={() => void runAction(
            "test-notification",
            gateway.testPush,
            i18n.t("settings.testNotificationSent"),
          )}
        />
      </Section>

      <Section title={i18n.t("settings.connectionActions")}>
        <Action
          label={i18n.t("common.reconnect")}
          detail={i18n.t("settings.reconnectDetail")}
          busy={working === "reconnect"}
          onPress={() => void runAction("reconnect", gateway.reconnect, i18n.t("settings.reconnected"))}
        />
        <Action
          label={i18n.t("settings.reauthenticate")}
          detail={i18n.t("settings.reauthenticateDetail")}
          busy={working === "reauth"}
          onPress={() => void runAction("reauth", gateway.reauthenticate, i18n.t("settings.reauthenticated"))}
        />
        <Action
          label={i18n.t("settings.repair")}
          detail={i18n.t("settings.repairDetail")}
          onPress={() => router.push("/pair")}
        />
        <Action
          label={i18n.t("settings.removeDevice")}
          detail={i18n.t("settings.removeDeviceDetail")}
          danger
          busy={working === "remove"}
          onPress={confirmRemoval}
        />
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </Section>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={advanced ? i18n.t("settings.collapseAdvanced") : i18n.t("settings.expandAdvanced")}
        style={styles.advancedToggle}
        onPress={() => setAdvanced((value) => !value)}
      >
        <Text style={styles.advancedTitle}>{i18n.t("settings.advanced")}</Text>
        <Text style={styles.advancedHint}>{advanced ? i18n.t("settings.collapse") : i18n.t("settings.advancedHint")}</Text>
      </Pressable>

      {advanced ? (
        <>
          <Section title={i18n.t("settings.runtimeStats")}>
            {diagnostic.status ? (
              <>
                <Metric label={i18n.t("settings.modelCalls")} value={diagnostic.status.model_calls} />
                <Metric label={i18n.t("settings.toolCalls")} value={diagnostic.status.tool_calls} />
                <Metric label="Token" value={diagnostic.status.total_tokens} />
                <Metric label={i18n.t("settings.cachedToken")} value={diagnostic.status.cached_tokens} />
              </>
            ) : <ActivityIndicator color={colors.accent} />}
          </Section>
          <Section title={i18n.t("settings.extensions")}>
            {diagnostic.extensions.length ? diagnostic.extensions.map((extension) => (
              <View key={`${extension.kind}:${extension.extension_id}`} style={styles.item}>
                <Text style={styles.itemTitle}>{extension.extension_id}</Text>
                <Text style={styles.meta}>{extension.kind.toUpperCase()} · {extension.state}</Text>
                {extension.detail ? <Text style={styles.detail}>{extension.detail}</Text> : null}
              </View>
            )) : <Text style={styles.empty}>{i18n.t("settings.noExtensions")}</Text>}
          </Section>
          <Section title={i18n.t("settings.availableTools", { count: diagnostic.tools.length })}>
            {diagnostic.tools.map((tool) => (
              <View key={tool.name} style={styles.item}>
                <Text style={styles.itemTitle}>{tool.name}</Text>
                <Text style={styles.meta}>{tool.origin_kind} · {tool.effect} · {tool.risk}</Text>
                {tool.requires_confirmation ? <Text style={styles.confirm}>{i18n.t("settings.confirmRequired")}</Text> : null}
              </View>
            ))}
          </Section>
          <Section title={i18n.t("settings.deviceAudit")}>
            {diagnostic.audit.slice(-20).reverse().map((event) => (
              <View key={String(event.event_id)} style={styles.item}>
                <Text style={styles.itemTitle}>{String(event.event_type)}</Text>
                <Text style={styles.meta}>{String(event.detail_code || "")}</Text>
              </View>
            ))}
          </Section>
        </>
      ) : null}
    </ScrollView>
  );
}

function pushRegistrationLabel(
  status: PushRegistrationStatus,
  translate: Translate,
): string {
  const keys: Record<PushRegistrationStatus, Parameters<Translate>[0]> = {
    checking: "settings.push.checking",
    registered: "settings.push.registered",
    not_configured: "settings.push.notConfigured",
    permission_denied: "settings.push.permissionDenied",
    token_failed: "settings.push.tokenFailed",
    server_failed: "settings.push.serverFailed",
  };
  return translate(keys[status]);
}

function LanguageChoice({ label, mode, selected, onPress }: { label: string; mode: LanguageMode; selected: boolean; onPress(mode: LanguageMode): Promise<void> }) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => void onPress(mode)}
      style={({ pressed }) => [styles.themeChoice, selected && styles.themeChoiceSelected, pressed && styles.pressed]}
    >
      <View style={[styles.radio, selected && styles.radioSelected]}>
        {selected ? <View style={styles.radioDot} /> : null}
      </View>
      <Text style={[styles.themeLabel, selected && styles.themeLabelSelected]}>{label}</Text>
    </Pressable>
  );
}

function ThemeChoice({ label, mode, selected, onPress }: { label: string; mode: ThemeMode; selected: boolean; onPress(mode: ThemeMode): Promise<void> }) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => void onPress(mode)}
      style={({ pressed }) => [styles.themeChoice, selected && styles.themeChoiceSelected, pressed && styles.pressed]}
    >
      <View style={[styles.radio, selected && styles.radioSelected]}>
        {selected ? <View style={styles.radioDot} /> : null}
      </View>
      <Text style={[styles.themeLabel, selected && styles.themeLabelSelected]}>{label}</Text>
    </Pressable>
  );
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
}

function Metric({ label, value, compact = false, tone = "normal" }: { label: string; value: unknown; compact?: boolean; tone?: "normal" | "danger" }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.meta}>{label}</Text>
      <Text numberOfLines={compact ? 1 : 2} style={[styles.metricValue, compact && styles.compact, tone === "danger" && styles.error]}>{String(value ?? "—")}</Text>
    </View>
  );
}

function Action({ label, detail, onPress, danger = false, busy = false }: { label: string; detail: string; onPress(): void; danger?: boolean; busy?: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={busy}
      style={({ pressed }) => [styles.action, pressed && styles.pressed]}
      onPress={onPress}
    >
      <View style={styles.actionCopy}>
        <Text style={[styles.actionTitle, danger && styles.danger]}>{label}</Text>
        <Text style={styles.meta}>{detail}</Text>
      </View>
      {busy ? <ActivityIndicator color={colors.accent} /> : <AppIcon name="chevron-right" color={danger ? colors.danger : colors.accent} size={19} />}
    </Pressable>
  );
}

function formatTime(timestamp: number, locale: string, never: string): string {
  if (!timestamp) return never;
  return new Date(timestamp * 1000).toLocaleString(locale, { hour12: false });
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  section: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 16, gap: 12 },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "700", marginBottom: 2 },
  themeHint: { color: colors.muted, fontSize: 13 },
  themeChoices: { flexDirection: "row", gap: 8 },
  themeChoice: { flex: 1, minHeight: 72, justifyContent: "center", alignItems: "center", gap: 8, borderRadius: 13, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background },
  themeChoiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentFaint },
  themeLabel: { color: colors.muted, fontSize: 13, fontWeight: "600", textAlign: "center" },
  themeLabelSelected: { color: colors.accent },
  radio: { width: 18, height: 18, borderRadius: 9, borderWidth: 1.5, borderColor: colors.lineStrong, alignItems: "center", justifyContent: "center" },
  radioSelected: { borderColor: colors.accent },
  radioDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.accent },
  metric: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 16 },
  metricValue: { color: colors.ink, fontWeight: "700", textAlign: "right", flexShrink: 1 },
  compact: { fontSize: 12 },
  action: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 12, flexDirection: "row", alignItems: "center", gap: 12 },
  actionCopy: { flex: 1, gap: 3 },
  actionTitle: { color: colors.accent, fontWeight: "700", fontSize: 16 },
  pressed: { opacity: 0.65 },
  advancedToggle: { borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 16, backgroundColor: colors.surface, gap: 4 },
  advancedTitle: { color: colors.ink, fontWeight: "700", fontSize: 16 },
  advancedHint: { color: colors.muted, fontSize: 13 },
  item: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 10, gap: 3 },
  itemTitle: { color: colors.ink, fontWeight: "600" },
  meta: { color: colors.muted, fontSize: 13 },
  detail: { color: colors.ink },
  confirm: { color: colors.warning, fontSize: 13 },
  empty: { color: colors.muted },
  message: { color: colors.ink, lineHeight: 20 },
  error: { color: colors.danger },
  danger: { color: colors.danger },
});
