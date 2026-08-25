import * as Application from "expo-application";
import { router } from "expo-router";
import { useEffect, useState, type ReactNode } from "react";
import { Alert, Linking, Pressable, ScrollView, Share, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n, type LanguageMode } from "@/i18n";
import { useThemePreference, type ThemeMode } from "@/state/ThemeProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { hasTaskNotificationPermission, requestTaskNotificationPermission, sendTestTaskNotification } from "@/notifications/taskNotifications";
import { appCacheSummary, clearAppCache, emptyAppCacheSummary, formatCacheBytes, type AppCacheSummary, type CacheKind } from "@/storage/appCache";
import { formatRelativeTime } from "@/ui/formatRelativeTime";
import {
  clearTransportDiagnostics,
  recentTransportStages,
  recentTransportSwitches,
  summarizeTransportProbes,
  transportDiagnosticSummaryText,
} from "@/api/transportDiagnostics";
import { transportLabelKey } from "@/api/transportPresentation";

const CACHE_KINDS: Exclude<CacheKind, "all">[] = ["conversation", "workspace", "task", "artifact"];

export default function AppSettingsScreen() {
  const theme = useThemePreference();
  const i18n = useI18n();
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [notificationWorking, setNotificationWorking] = useState(false);
  const [notificationTesting, setNotificationTesting] = useState(false);
  const [cache, setCache] = useState<AppCacheSummary>(() => emptyAppCacheSummary());
  const [cacheWorking, setCacheWorking] = useState(false);
  const [diagnosticRevision, setDiagnosticRevision] = useState(0);

  useEffect(() => {
    void hasTaskNotificationPermission().then(setNotificationsEnabled);
    void appCacheSummary().then(setCache);
  }, []);

  async function enableNotifications() {
    setNotificationWorking(true);
    setNotificationsEnabled(await requestTaskNotificationPermission());
    setNotificationWorking(false);
  }

  async function sendTestNotification() {
    setNotificationTesting(true);
    const delivered = await sendTestTaskNotification(
      i18n.t("settings.notificationsTestTitle"),
      i18n.t("settings.notificationsTestBody"),
    );
    setNotificationTesting(false);
    Alert.alert(i18n.t(delivered ? "settings.notificationsTestSent" : "settings.notificationsTestFailed"));
  }

  function confirmClearCache(kind: CacheKind) {
    Alert.alert(
      kind === "all" ? i18n.t("settings.cacheClearTitle") : i18n.t("settings.cacheClearKindTitle"),
      kind === "all" ? i18n.t("settings.cacheClearBody") : i18n.t("settings.cacheClearKindBody"),
      [
        { text: i18n.t("common.cancel"), style: "cancel" },
        {
          text: i18n.t("settings.cacheClear"),
          style: "destructive",
          onPress: () => {
            setCacheWorking(true);
            const result = clearAppCache(kind);
            void appCacheSummary().then((next) => setCache(next));
            setCacheWorking(false);
            if (result.failed) {
              Alert.alert(i18n.t("settings.cacheClearFailed", { count: result.failed }));
            } else {
              Alert.alert(i18n.t("settings.cacheCleared", { count: result.removed }));
            }
          },
        },
      ],
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Section title={i18n.t("settings.appearance")} detail={i18n.t("settings.appearanceHint")}>
        <View accessibilityRole="radiogroup" style={styles.choices}>
          <Choice label={i18n.t("settings.theme.system")} mode="system" selected={theme.mode === "system"} onPress={theme.setMode} />
          <Choice label={i18n.t("settings.theme.light")} mode="light" selected={theme.mode === "light"} onPress={theme.setMode} />
          <Choice label={i18n.t("settings.theme.dark")} mode="dark" selected={theme.mode === "dark"} onPress={theme.setMode} />
        </View>
      </Section>

      <Section title={i18n.t("settings.language")} detail={i18n.t("settings.languageHint")}>
        <View accessibilityRole="radiogroup" style={styles.choices}>
          <Choice label={i18n.t("settings.language.system")} mode="system" selected={i18n.mode === "system"} onPress={i18n.setMode} />
          <Choice label={i18n.t("settings.language.zh")} mode="zh-CN" selected={i18n.mode === "zh-CN"} onPress={i18n.setMode} />
          <Choice label={i18n.t("settings.language.en")} mode="en-US" selected={i18n.mode === "en-US"} onPress={i18n.setMode} />
        </View>
      </Section>

      <Section title={i18n.t("settings.notifications")} detail={i18n.t("settings.notificationsHint")}>
        <Text style={notificationsEnabled ? styles.enabled : styles.disabled}>
          {notificationsEnabled ? i18n.t("settings.notificationsEnabled") : i18n.t("settings.notificationsDisabled")}
        </Text>
        {notificationsEnabled ? (
          <View style={styles.notificationActions}>
            <AppPressable disabled={notificationTesting} onPress={() => void sendTestNotification()} style={styles.updateButton}>
              <Text style={styles.updateText}>{i18n.t("settings.notificationsTest")}</Text>
            </AppPressable>
          </View>
        ) : null}
        {!notificationsEnabled ? (
          <View style={styles.notificationActions}>
            <AppPressable disabled={notificationWorking} onPress={() => void enableNotifications()} style={styles.updateButton}>
              <Text style={styles.updateText}>{notificationWorking ? i18n.t("settings.notificationsWorking") : i18n.t("settings.notificationsEnable")}</Text>
            </AppPressable>
            <AppPressable onPress={() => void Linking.openSettings()} style={styles.settingsButton}>
              <Text style={styles.settingsButtonText}>{i18n.t("settings.notificationsOpenSettings")}</Text>
            </AppPressable>
          </View>
        ) : null}
      </Section>

      <Section title={i18n.t("settings.transportDiagnostics")} detail={i18n.t("settings.transportDiagnosticsHint")}>
        {(() => {
          const summary = summarizeTransportProbes();
          if (!summary.total) return <Text style={styles.detail}>{i18n.t("settings.transportNoData")}</Text>;
          return (
            <>
              {(Object.keys(summary.byMode) as Array<keyof typeof summary.byMode>).filter((mode) => summary.byMode[mode].count).map((mode) => (
                <Text key={mode} style={styles.detail}>
                  {i18n.t("settings.transportModeLine", {
                    mode: i18n.t(transportLabelKey(mode)),
                    count: summary.byMode[mode].count,
                    failed: summary.byMode[mode].failed,
                  })}
                </Text>
              ))}
              <Text style={styles.detail}>{i18n.t("settings.transportAverage", { ms: summary.averageMs })}</Text>
              {recentTransportStages().slice(-8).map((event, index) => (
                <Text key={`${event.attemptId}-${event.stage}-${index}`} style={styles.detail}>
                  {i18n.t("settings.transportStageLine", {
                    stage: event.stage,
                    outcome: event.outcome,
                    ms: event.endedAt - event.startedAt,
                  })}
                </Text>
              ))}
              {recentTransportSwitches().slice(-3).map((event, index) => (
                <Text key={`${event.attemptId}-${event.at}-${index}`} style={styles.detail}>
                  {i18n.t("settings.transportSwitchLine", { from: event.from, to: event.to, reason: event.reasonCode })}
                </Text>
              ))}
            </>
          );
        })()}
        <View style={styles.notificationActions} key={diagnosticRevision}>
          <AppPressable onPress={() => void Share.share({ message: transportDiagnosticSummaryText() })} style={styles.settingsButton}>
            <Text style={styles.settingsButtonText}>{i18n.t("settings.transportCopy")}</Text>
          </AppPressable>
          <AppPressable onPress={() => { clearTransportDiagnostics(); setDiagnosticRevision((value) => value + 1); }} style={styles.settingsButton}>
            <Text style={styles.settingsButtonText}>{i18n.t("settings.transportClear")}</Text>
          </AppPressable>
        </View>
      </Section>

      <Section title={i18n.t("settings.cache")} detail={i18n.t("settings.cacheDetail")}>
        <Text style={styles.detail}>
          {i18n.t("settings.cacheUsage", { size: formatCacheBytes(cache.bytes), files: cache.files })}
          {cache.updatedAt ? `\n${i18n.t("settings.cacheUpdated", { time: formatRelativeTime(cache.updatedAt, i18n.locale) })}` : ""}
        </Text>
        {CACHE_KINDS.map((kind) => (
          <View key={kind} style={styles.cacheKindRow}>
            <View style={styles.flex}>
              <Text style={styles.cacheKindLabel}>{i18n.t(`settings.cacheKind.${kind}` as never)}</Text>
              <Text style={styles.detail}>
                {i18n.t("settings.cacheKindUsage", { size: formatCacheBytes(cache.byKind[kind].bytes), files: cache.byKind[kind].files })}
              </Text>
            </View>
            <AppPressable
              disabled={cacheWorking || !cache.byKind[kind].files}
              onPress={() => confirmClearCache(kind)}
              style={styles.cacheKindClear}
            >
              <Text style={styles.settingsButtonText}>{i18n.t("settings.cacheClear")}</Text>
            </AppPressable>
          </View>
        ))}
        <AppPressable disabled={cacheWorking} onPress={() => confirmClearCache("all")} style={styles.settingsButton}>
          <Text style={styles.settingsButtonText}>{cacheWorking ? i18n.t("settings.cacheClearWorking") : i18n.t("settings.cacheClearAll")}</Text>
        </AppPressable>
      </Section>

      <View style={styles.card}>
        <View style={styles.versionRow}>
          <View style={styles.versionIcon}><AppIcon name="settings" color={colors.accent} size={23} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{i18n.t("settings.appVersion")}</Text>
            <Text style={styles.detail}>
              {Application.nativeApplicationVersion ?? i18n.t("settings.development")} ({Application.nativeBuildVersion ?? "—"})
            </Text>
          </View>
        </View>
        <AppPressable onPress={() => router.push("/update")} style={styles.updateButton}>
          <AppIcon name="refresh" color={colors.onAccent} size={20} />
          <Text style={styles.updateText}>{i18n.t("settings.checkAppUpdate")}</Text>
        </AppPressable>
        <Text style={styles.updateHint}>{i18n.t("settings.checkAppUpdateHint")}</Text>
      </View>
    </ScrollView>
  );
}

function Section({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.detail}>{detail}</Text>
      {children}
    </View>
  );
}

function Choice<T extends ThemeMode | LanguageMode>({
  label,
  mode,
  selected,
  onPress,
}: {
  label: string;
  mode: T;
  selected: boolean;
  onPress(mode: T): Promise<void>;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => void onPress(mode)}
      style={({ pressed }) => [styles.choice, selected && styles.choiceSelected, pressed && styles.pressed]}
    >
      <View style={[styles.radio, selected && styles.radioSelected]}>{selected ? <View style={styles.radioDot} /> : null}</View>
      <Text style={[styles.choiceLabel, selected && styles.choiceLabelSelected]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.large, paddingBottom: 48 },
  card: { padding: spacing.large, gap: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  title: { color: colors.ink, ...typography.subheading, fontWeight: "800" },
  detail: { color: colors.muted, ...typography.caption, lineHeight: 19 },
  choices: { gap: spacing.small, marginTop: 2 },
  choice: { minHeight: 48, flexDirection: "row", alignItems: "center", gap: spacing.medium, paddingHorizontal: spacing.medium, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line },
  choiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  choiceLabel: { color: colors.ink, fontWeight: "700" },
  choiceLabelSelected: { color: colors.accent, fontWeight: "800" },
  radio: { width: 20, height: 20, alignItems: "center", justifyContent: "center", borderRadius: radii.small, borderWidth: 2, borderColor: colors.muted },
  radioSelected: { borderColor: colors.accent },
  radioDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent },
  pressed: { opacity: 0.72 },
  versionRow: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  versionIcon: { width: 46, height: 46, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  updateButton: { minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small, borderRadius: radii.medium, backgroundColor: colors.accent },
  updateText: { color: colors.onAccent, fontWeight: "800" },
  updateHint: { color: colors.muted, ...typography.small, lineHeight: 18 },
  enabled: { color: colors.accent, fontWeight: "800" },
  disabled: { color: colors.warning, fontWeight: "800" },
  notificationActions: { gap: spacing.small },
  settingsButton: { minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line },
  settingsButtonText: { color: colors.accent, fontWeight: "800" },
  cacheKindRow: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  cacheKindLabel: { color: colors.ink, fontWeight: "700" },
  cacheKindClear: { minHeight: 36, paddingHorizontal: spacing.medium, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line },
});
