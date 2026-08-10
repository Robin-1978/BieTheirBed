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

import { GatewayError } from "@/api/gatewayClient";
import type { AndroidRelease } from "@/api/models";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import {
  AndroidUpdateDownload,
  installAndroidPackage,
  installedAndroidVersionCode,
  isAndroidUpdateAvailable,
  openUnknownSourcesSettings,
  type AndroidUpdateProgress,
} from "@/update/androidUpdater";
import { requiresAndroidUpdate } from "@/update/releasePolicy";

type DownloadState = "idle" | "downloading" | "paused" | "ready" | "error";

export default function UpdateScreen() {
  const gateway = useGateway();
  const [release, setRelease] = useState<AndroidRelease | null>(null);
  const [checking, setChecking] = useState(true);
  const [state, setState] = useState<DownloadState>("idle");
  const [progress, setProgress] = useState<AndroidUpdateProgress>({ downloaded: 0, total: 0 });
  const [packageUri, setPackageUri] = useState("");
  const [message, setMessage] = useState("");
  const download = useRef<AndroidUpdateDownload | null>(null);
  const pausing = useRef(false);
  const currentVersionCode = installedAndroidVersionCode();

  useEffect(() => {
    if (!gateway.client) return;
    setChecking(true);
    gateway.client.latestAndroidRelease()
      .then(setRelease)
      .catch((error: unknown) => {
        if (!(error instanceof GatewayError && error.status === 404)) {
          setMessage(error instanceof Error ? error.message : "检查更新失败");
        }
      })
      .finally(() => setChecking(false));
  }, [gateway.client]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next) => {
      if (next !== "active" && state === "downloading") void pauseDownload();
    });
    return () => subscription.remove();
  }, [state]);

  useEffect(() => () => {
    if (download.current) void download.current.pause();
  }, []);

  async function startDownload() {
    if (!release || !gateway.sessionToken) return;
    setMessage("");
    setPackageUri("");
    setState("downloading");
    pausing.current = false;
    try {
      const controller = await AndroidUpdateDownload.create({
        gatewayUrl: gateway.gatewayUrl,
        token: gateway.sessionToken,
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
      download.current = null;
      if (pausing.current) return;
      setState("error");
      setMessage(error instanceof Error ? error.message : "更新包下载失败");
    }
  }

  async function pauseDownload() {
    if (!download.current || state !== "downloading") return;
    pausing.current = true;
    try {
      await download.current.pause();
      download.current = null;
      setState("paused");
    } catch (error) {
      pausing.current = false;
      setMessage(error instanceof Error ? error.message : "保存下载断点失败");
    }
  }

  async function install() {
    if (!packageUri) return;
    try {
      await installAndroidPackage(packageUri);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法打开系统安装器");
    }
  }

  const available = release ? isAndroidUpdateAvailable(release, currentVersionCode) : false;
  const fraction = progress.total > 0 ? Math.min(1, progress.downloaded / progress.total) : 0;

  if (Platform.OS !== "android") {
    return <View style={styles.center}><Text style={styles.message}>私人自更新目前仅支持 Android</Text></View>;
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.card}>
        <Text style={styles.title}>小诺私人更新通道</Text>
        <Text style={styles.meta}>
          当前版本 {Application.nativeApplicationVersion ?? "—"} · build {currentVersionCode}
        </Text>
        {checking ? <ActivityIndicator color={colors.accent} /> : null}
        {message && !release ? <Text style={styles.error}>{message}</Text> : null}
        {!checking && !release ? <Text style={styles.message}>暂时没有发布安装包</Text> : null}
        {release ? (
          <>
            <View style={styles.releaseHeader}>
              <Text style={styles.releaseName}>{release.version_name}</Text>
              <Text style={[styles.badge, available && styles.badgeActive]}>
                {available ? "发现更新" : "已是最新"}
              </Text>
            </View>
            <Text style={styles.meta}>
              build {release.version_code} · {formatBytes(release.size_bytes)}
            </Text>
            {requiresAndroidUpdate(release, currentVersionCode) ? (
              <Text style={styles.required}>当前版本过旧，需要更新后继续使用</Text>
            ) : null}
            {release.release_notes ? <Text style={styles.notes}>{release.release_notes}</Text> : null}
          </>
        ) : null}
      </View>

      {release && available ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>下载安装包</Text>
          {(state === "downloading" || state === "paused" || state === "ready") ? (
            <>
              <View style={styles.track}><View style={[styles.bar, { width: `${fraction * 100}%` }]} /></View>
              <Text style={styles.meta}>{formatBytes(progress.downloaded)} / {formatBytes(progress.total || release.size_bytes)}</Text>
            </>
          ) : null}
          {message ? <Text style={styles.error}>{message}</Text> : null}
          <View style={styles.row}>
            {state === "downloading" ? (
              <Button label="暂停" secondary onPress={() => void pauseDownload()} />
            ) : null}
            {state === "idle" || state === "paused" || state === "error" ? (
              <Button label={state === "paused" ? "继续下载" : "下载更新"} onPress={() => void startDownload()} />
            ) : null}
            {state === "ready" ? <Button label="安装更新" onPress={() => void install()} /> : null}
          </View>
          <Text style={styles.tip}>离开页面或 App 进入后台时会保存断点，下次从断点继续。</Text>
          <Pressable onPress={() => void openUnknownSourcesSettings()}>
            <Text style={styles.link}>允许小诺安装未知来源应用</Text>
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
