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
import { colors } from "@/theme";

type AccountMode = "login" | "register" | "recover";

export default function AccountLoginScreen() {
  const [hubUrl, setHubUrl] = useState("https://knoa.tinydotdot.com");
  const [mode, setMode] = useState<AccountMode>("login");
  const [loginIdentity, setLoginIdentity] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [setupPayload, setSetupPayload] = useState("");
  const [scanning, setScanning] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [message, setMessage] = useState("");
  const [working, setWorking] = useState(false);

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
          displayName || loginIdentity.split("@")[0] || "Knoa User",
          password,
        );
      } else {
        await resetHostedPassword(setupPayload, password);
      }
      setPassword("");
      router.replace("/account");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hub 帐号操作失败");
    } finally {
      setWorking(false);
    }
  }

  async function openScanner() {
    if (!cameraPermission?.granted) {
      const result = await requestCameraPermission();
      if (!result.granted) {
        setMessage("需要相机权限才能扫描 Hub 一次性二维码");
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
        <Text style={styles.scanHint}>扫描 Hub 发出的帐号注册或密码恢复二维码</Text>
        <AppPressable style={styles.cancelScan} onPress={() => setScanning(false)}>
          <Text style={styles.primaryText}>取消</Text>
        </AppPressable>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>KNOA ACCOUNT</Text>
        <Text style={styles.title}>登录 Hub</Text>
        <Text style={styles.hint}>先建立帐号与 Workspace。Node 是进入 Workspace 后选择的执行位置。</Text>
      </View>
      <View style={styles.card}>
        <TextInput value={hubUrl} onChangeText={setHubUrl} placeholder="https://hub.example.com" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
        <View style={styles.modeRow}>
          {(["login", "register", "recover"] as const).map((item) => (
            <AppPressable key={item} disabled={working} onPress={() => { setMode(item); setMessage(""); }} style={[styles.mode, mode === item && styles.modeActive]}>
              <Text style={mode === item ? styles.modeTextActive : styles.modeText}>
                {item === "login" ? "登录" : item === "register" ? "创建帐号" : "恢复密码"}
              </Text>
            </AppPressable>
          ))}
        </View>
        {mode !== "login" ? (
          <>
            <AppPressable style={styles.secondary} disabled={working} onPress={() => void openScanner()}>
              <Text style={styles.secondaryText}>扫描 Hub 一次性二维码</Text>
            </AppPressable>
            <TextInput value={setupPayload} onChangeText={setSetupPayload} placeholder="或粘贴 Hub 一次性凭据" placeholderTextColor={colors.muted} autoCapitalize="none" multiline style={[styles.input, styles.payload]} />
          </>
        ) : null}
        {mode !== "recover" ? <TextInput value={loginIdentity} onChangeText={setLoginIdentity} placeholder="登录标识" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} /> : null}
        {mode === "register" ? <TextInput value={displayName} onChangeText={setDisplayName} placeholder="显示名称" placeholderTextColor={colors.muted} style={styles.input} /> : null}
        <TextInput value={password} onChangeText={setPassword} placeholder={mode === "recover" ? "设置新密码（至少 12 位）" : "帐号密码（至少 12 位）"} placeholderTextColor={colors.muted} secureTextEntry style={styles.input} />
        <AppPressable style={styles.primary} disabled={working} onPress={() => void submit()}>
          {working ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>{mode === "login" ? "登录 Hub" : mode === "register" ? "创建并登录" : "恢复并登录"}</Text>}
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
  scanner: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#000" },
  scanFrame: { width: 260, height: 260, borderWidth: 3, borderColor: "#fff", borderRadius: 22 },
  scanHint: { color: "#fff", marginTop: 22, paddingHorizontal: 30, textAlign: "center", fontWeight: "700" },
  cancelScan: { position: "absolute", bottom: 48, backgroundColor: colors.accent, borderRadius: 14, paddingHorizontal: 24, paddingVertical: 13 },
});
