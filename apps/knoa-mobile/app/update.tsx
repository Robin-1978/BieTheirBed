import * as Application from "expo-application";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { AndroidRelease } from "@/api/models";
import { resolveAndroidRelease } from "@/hub/hubClient";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { useI18n } from "@/i18n";
import {
  AndroidUpdateDownload,
  installAndroidPackage,
  installedAndroidVersionCode,
  isAndroidUpdateAvailable,
  loadAndroidUpdateCheckpoint,
  loadReadyAndroidPackage,
  openUnknownSourcesSettings,
  type AndroidUpdateProgress,
} from "@/update/androidUpdater";
import { requiresAndroidUpdate } from "@/update/releasePolicy";

type DownloadState = "idle" | "downloading" | "paused" | "ready" | "error";

export default function UpdateScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [release, setRelease] = useState<AndroidRelease | null>(null);
  const [checking, setChecking] = useState(true);
  const [state, setState] = useState<DownloadState>("idle");
  const [progress, setProgress] = useState<AndroidUpdateProgress>({ downloaded: 0, total: 0 });
  const [packageUri, setPackageUri] = useState("");
  const [message, setMessage] = useState("");
  const download = useRef<AndroidUpdateDownload | null>(null);
  const pausing = useRef(false);
  const installAfterSettings = useRef(false);
  const currentVersionCode = installedAndroidVersionCode();

  useEffect(() => {
    if (!gateway.client) return;
    setChecking(true);
    gateway.runAuthenticated((client) => resolveAndroidRelease(() => client.latestAndroidRelease()))
      .then(setRelease)
      .catch(() => setMessage(t("update.checkFailed")))
      .finally(() => setChecking(false));
  }, [gateway.client, gateway.runAuthenticated, t]);

  useEffect(() => {
    if (!release || !isAndroidUpdateAvailable(release, currentVersionCode)) return;
    let cancelled = false;
    void (async () => {
      const uri = await loadReadyAndroidPackage(release);
      if (cancelled) return;
      if (uri) {
        setPackageUri(uri);
        setProgress({ downloaded: release.size_bytes, total: release.size_bytes });
        setState("ready");
        return;
      }
      const checkpoint = await loadAndroidUpdateCheckpoint(release);
      if (cancelled || !checkpoint) return;
      setProgress(checkpoint);
      setState("paused");
    })().catch(() => {
      // A corrupt or unavailable checkpoint must not block a fresh download.
    });
    return () => {
      cancelled = true;
    };
  }, [currentVersionCode, release]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next) => {
      if (next !== "active" && state === "downloading") void pauseDownload();
      if (next === "active" && installAfterSettings.current && packageUri) {
        installAfterSettings.current = false;
        void installAndroidPackage(packageUri).catch((error) => {
          setMessage(t("update.installerFailed"));
        });
      }
    });
    return () => subscription.remove();
  }, [packageUri, state, t]);

  useEffect(() => () => {
    if (download.current) void download.current.pause();
  }, []);

  async function startDownload() {
    if (!release) return;
    setMessage("");
    setPackageUri("");
    setState("downloading");
    pausing.current = false;
    let controller: AndroidUpdateDownload | null = null;
    try {
      controller = await AndroidUpdateDownload.create({
        gatewayUrl: gateway.gatewayUrl,
        release,
        onProgress: setProgress,
      });
      download.current = controller;
      const uri = await controller.start();
      download.current = null;
      setPackageUri(uri);
      setProgress({ downloaded: release.size_bytes, total: release.size_bytes });
      setState("ready");
    } catch (error) {
      if (pausing.current) return;
      const checkpoint = await controller?.preserveCheckpoint().catch(() => null);
      download.current = null;
      if (checkpoint) {
        setProgress(checkpoint);
        setState("paused");
        setMessage(t("update.downloadInterrupted"));
      } else {
        setState("error");
        setMessage(t("update.downloadFailed"));
      }
    }
  }

  async function pauseDownload() {
    if (!download.current || state !== "downloading") return;
    pausing.current = true;
    try {
      const checkpoint = await download.current.pause();
      download.current = null;
      setProgress(checkpoint);
      setState("paused");
    } catch (error) {
      pausing.current = false;
      setMessage(t("update.pauseFailed"));
    }
  }

  async function install() {
    if (!packageUri) return;
    try {
      await installAndroidPackage(packageUri);
    } catch (error) {
      setMessage(t("update.installerFailed"));
    }
  }

  async function allowUnknownSources() {
    installAfterSettings.current = Boolean(packageUri);
    try {
      await openUnknownSourcesSettings();
    } catch (error) {
      installAfterSettings.current = false;
      setMessage(t("update.permissionSettingsFailed"));
    }
  }

  const available = release ? isAndroidUpdateAvailable(release, currentVersionCode) : false;
  const fraction = progress.total > 0 ? Math.min(1, progress.downloaded / progress.total) : 0;

  if (Platform.OS !== "android") {
    return <View style={styles.center}><Text style={styles.message}>{t("update.androidOnly")}</Text></View>;
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.card}>
        <Text style={styles.title}>{t("app.name")}</Text>
        <Text style={styles.meta}>
          {t("update.currentVersion", { version: Application.nativeApplicationVersion ?? "—", build: currentVersionCode })}
        </Text>
        {checking ? <ActivityIndicator color={colors.accent} /> : null}
        {message && !release ? <Text style={styles.error}>{message}</Text> : null}
        {!checking && !release ? <Text style={styles.message}>{t("update.noRelease")}</Text> : null}
        {release ? (
          <>
            <View style={styles.releaseHeader}>
              <Text style={styles.releaseName}>{release.version_name}</Text>
              <Text style={[styles.badge, available && styles.badgeActive]}>
                {available ? t("update.available") : t("update.latest")}
              </Text>
            </View>
            <Text style={styles.meta}>
              build {release.version_code} · {formatBytes(release.size_bytes)}
            </Text>
            {requiresAndroidUpdate(release, currentVersionCode) ? (
              <Text style={styles.required}>{t("update.required")}</Text>
            ) : null}
            {release.release_notes ? <Text style={styles.notes}>{release.release_notes}</Text> : null}
          </>
        ) : null}
      </View>

      {release && available ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>{t("update.downloadPackage")}</Text>
          {(state === "downloading" || state === "paused" || state === "ready") ? (
            <>
              <View style={styles.track}><View style={[styles.bar, { width: `${fraction * 100}%` }]} /></View>
              <Text style={styles.meta}>{formatBytes(progress.downloaded)} / {formatBytes(progress.total || release.size_bytes)}</Text>
            </>
          ) : null}
          {message ? <Text style={styles.error}>{message}</Text> : null}
          <View style={styles.row}>
            {state === "downloading" ? (
              <Button label={t("update.pause")} secondary onPress={() => void pauseDownload()} />
            ) : null}
            {state === "idle" || state === "paused" || state === "error" ? (
              <Button
                label={state === "paused" ? t("update.resume") : state === "error" ? t("update.retry") : t("update.download")}
                onPress={() => void startDownload()}
              />
            ) : null}
            {state === "ready" ? <Button label={t("update.install")} onPress={() => void install()} /> : null}
          </View>
          <Text style={styles.tip}>{t("update.resumeHint")}</Text>
          <Pressable onPress={() => void allowUnknownSources()}>
            <Text style={styles.link}>{t("update.allowUnknown")}</Text>
          </Pressable>
        </View>
      ) : null}
    </ScrollView>
  );
}

function Button({ label, secondary = false, onPress }: { label: string; secondary?: boolean; onPress(): void }) {
  return (
    <Pressable style={[styles.button, secondary && styles.buttonSecondary]} onPress={onPress}>
      <Text style={[styles.buttonText, secondary && styles.buttonSecondaryText]}>{label}</Text>
    </Pressable>
  );
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 MB";
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  card: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 18, gap: 12 },
  title: { color: colors.ink, fontSize: 20, fontWeight: "700" },
  sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  releaseHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  releaseName: { color: colors.ink, fontSize: 22, fontWeight: "700" },
  badge: { color: colors.muted, backgroundColor: colors.background, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 12 },
  badgeActive: { color: colors.accent, backgroundColor: colors.accentSoft, fontWeight: "700" },
  meta: { color: colors.muted, fontSize: 13 },
  message: { color: colors.muted },
  notes: { color: colors.ink, lineHeight: 22 },
  required: { color: colors.danger, fontWeight: "600" },
  track: { height: 8, borderRadius: 4, overflow: "hidden", backgroundColor: colors.background },
  bar: { height: 8, borderRadius: 4, backgroundColor: colors.accent },
  row: { flexDirection: "row", gap: 10 },
  button: { flex: 1, alignItems: "center", backgroundColor: colors.accent, padding: 13, borderRadius: 13 },
  buttonSecondary: { backgroundColor: colors.accentSoft },
  buttonText: { color: "white", fontWeight: "700" },
  buttonSecondaryText: { color: colors.accent },
  error: { color: colors.danger, lineHeight: 21 },
  tip: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  link: { color: colors.accent, fontWeight: "600" },
});
