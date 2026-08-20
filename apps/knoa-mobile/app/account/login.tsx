import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import {
  loadHubConnection,
  loginHostedAccount,
  registerHostedAccount,
  resetHostedPassword,
} from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

type AccountMode = "login" | "register" | "recover";
const HOSTED_HUB_URL = "https://knoa.tinydotdot.com";

export default function AccountLoginScreen() {
  const { t } = useI18n();
  const [hubUrl, setHubUrl] = useState(HOSTED_HUB_URL);
  const [mode, setMode] = useState<AccountMode>("login");
  const [loginIdentity, setLoginIdentity] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [setupPayload, setSetupPayload] = useState("");
  const [scanning, setScanning] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [message, setMessage] = useState("");
  const [working, setWorking] = useState(false);
  const [selfHosted, setSelfHosted] = useState(false);

  useEffect(() => {
    void loadHubConnection().then((connection) => {
      if (connection) router.replace("/account");
    });
  }, []);

  async function submit() {
    setWorking(true);
    setMessage("");
    try {
      if (mode === "login") {
        await loginHostedAccount(hubUrl, loginIdentity, password);
      } else if (mode === "register") {
        await registerHostedAccount(
          setupPayload,
          loginIdentity,
          displayName || loginIdentity.split("@")[0] || t("login.defaultDisplayName"),
          password,
        );
      } else {
        await resetHostedPassword(setupPayload, password);
      }
      setPassword("");
      router.replace("/account");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("login.failed"));
    } finally {
      setWorking(false);
    }
  }

  async function openScanner() {
    if (!cameraPermission?.granted) {
      const result = await requestCameraPermission();
      if (!result.granted) {
        setMessage(t("login.cameraRequired"));
        return;
      }
    }
    setScanning(true);
  }

  if (scanning && cameraPermission?.granted) {
    return (
      <View style={styles.scanner}>
        <CameraView
          style={StyleSheet.absoluteFill}
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={({ data }) => {
            try {
              const version = (JSON.parse(data) as { version?: string }).version;
              setMode(version === "knoa-hosted-password-reset-v1" ? "recover" : "register");
            } catch {
              setMode("register");
            }
            setSetupPayload(data);
            setScanning(false);
            setMessage("");
          }}
        />
        <View style={styles.scanFrame} />
        <Text style={styles.scanHint}>{t("login.scanHint")}</Text>
        <AppPressable style={styles.cancelScan} onPress={() => setScanning(false)}>
          <Text style={styles.primaryText}>{t("common.cancel")}</Text>
        </AppPressable>
      </View>
    );
  }

  const modeLabels = {
    login: t("login.modeLogin"),
    register: t("login.modeRegister"),
    recover: t("login.modeRecover"),
  } as const;

  const submitLabel = mode === "login"
    ? t("login.submitLogin")
    : mode === "register"
      ? t("login.submitRegister")
      : t("login.submitRecover");

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>{t("login.eyebrow")}</Text>
        <Text style={styles.title}>{selfHosted ? t("login.titleSelfHosted") : t("login.titleHosted")}</Text>
        <Text style={styles.hint}>{t("login.hint")}</Text>
      </View>
      <View style={styles.card}>
        {selfHosted ? (
          <TextInput
            value={hubUrl}
            onChangeText={setHubUrl}
            placeholder={t("login.hubUrlPlaceholder")}
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            style={styles.input}
          />
        ) : null}
        <View style={styles.modeRow}>
          {(["login", "register", "recover"] as const).map((item) => (
            <AppPressable key={item} disabled={working} onPress={() => { setMode(item); setMessage(""); }} style={[styles.mode, mode === item && styles.modeActive]}>
              <Text style={mode === item ? styles.modeTextActive : styles.modeText}>{modeLabels[item]}</Text>
            </AppPressable>
          ))}
        </View>
        {mode !== "login" ? (
          <>
            <AppPressable style={styles.secondary} disabled={working} onPress={() => void openScanner()}>
              <Text style={styles.secondaryText}>{t("login.scanQr")}</Text>
            </AppPressable>
            <TextInput
              value={setupPayload}
              onChangeText={setSetupPayload}
              placeholder={t("login.setupPlaceholder")}
              placeholderTextColor={colors.muted}
              autoCapitalize="none"
              multiline
              style={[styles.input, styles.payload]}
            />
          </>
        ) : null}
        {mode !== "recover" ? (
          <TextInput
            value={loginIdentity}
            onChangeText={setLoginIdentity}
            placeholder={t("login.identityPlaceholder")}
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            style={styles.input}
          />
        ) : null}
        {mode === "register" ? (
          <TextInput
            value={displayName}
            onChangeText={setDisplayName}
            placeholder={t("login.displayNamePlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.input}
          />
        ) : null}
        <TextInput
          value={password}
          onChangeText={setPassword}
          placeholder={mode === "recover" ? t("login.newPasswordPlaceholder") : t("login.passwordPlaceholder")}
          placeholderTextColor={colors.muted}
          secureTextEntry
          style={styles.input}
        />
        <AppPressable style={styles.primary} disabled={working} onPress={() => void submit()}>
          {working ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>{submitLabel}</Text>}
        </AppPressable>
        <AppPressable
          disabled={working}
          onPress={() => {
            const next = !selfHosted;
            setSelfHosted(next);
            if (!next) setHubUrl(HOSTED_HUB_URL);
          }}
          style={styles.advanced}
        >
          <Text style={styles.advancedText}>{selfHosted ? t("login.useHosted") : t("login.useSelfHosted")}</Text>
        </AppPressable>
        {message ? <Text style={styles.error}>{message}</Text> : null}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, justifyContent: "center", padding: 22, gap: 18, backgroundColor: colors.background },
  hero: { gap: 7 },
  eyebrow: { color: colors.accent, fontSize: 11, letterSpacing: 2, fontWeight: "800" },
  title: { color: colors.ink, fontSize: 28, fontWeight: "800" },
  hint: { color: colors.muted, lineHeight: 20 },
  card: { backgroundColor: colors.surface, borderRadius: 20, borderWidth: 1, borderColor: colors.line, padding: 17, gap: 12 },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 13, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
  payload: { minHeight: 76, textAlignVertical: "top" },
  modeRow: { flexDirection: "row", gap: 7 },
  mode: { flex: 1, alignItems: "center", paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: colors.line },
  modeActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  modeText: { color: colors.muted, fontWeight: "700" },
  modeTextActive: { color: "white", fontWeight: "800" },
  primary: { backgroundColor: colors.accent, borderRadius: 13, padding: 14, alignItems: "center" },
  primaryText: { color: "white", fontWeight: "800" },
  secondary: { borderWidth: 1, borderColor: colors.accent, borderRadius: 13, padding: 13, alignItems: "center" },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  error: { color: colors.danger, textAlign: "center" },
  advanced: { minHeight: 42, alignItems: "center", justifyContent: "center" },
  advancedText: { color: colors.muted, fontWeight: "700" },
  scanner: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#000" },
  scanFrame: { width: 260, height: 260, borderWidth: 3, borderColor: "#fff", borderRadius: 22 },
  scanHint: { color: "#fff", marginTop: 22, paddingHorizontal: 30, textAlign: "center", fontWeight: "700" },
  cancelScan: { position: "absolute", bottom: 48, backgroundColor: colors.accent, borderRadius: 14, paddingHorizontal: 24, paddingVertical: 13 },
});
