import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function PairScreen() {
  const gateway = useGateway();
  const [permission, requestPermission] = useCameraPermissions();
  const [displayName, setDisplayName] = useState("我的手机");
  const [payload, setPayload] = useState("");
  const [scanning, setScanning] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  async function submit(encoded = payload) {
    if (!encoded.trim() || !displayName.trim() || working) return;
    setWorking(true);
    setError("");
    try {
      await gateway.pair(encoded.trim(), displayName.trim());
      router.replace("/chat");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "配对失败");
      setScanning(false);
    } finally {
      setWorking(false);
    }
  }

  if (scanning && permission?.granted) {
    return (
      <View style={styles.scanner}>
        <CameraView
          style={StyleSheet.absoluteFill}
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={({ data }) => {
            setScanning(false);
            setPayload(data);
            void submit(data);
          }}
        />
        <View style={styles.scanFrame} />
        <Pressable style={styles.cancel} onPress={() => setScanning(false)}>
          <Text style={styles.cancelText}>取消扫描</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Text style={styles.lead}>在电脑上运行 `pca gateway pair`，然后扫描二维码。</Text>
      <TextInput
        style={styles.input}
        value={displayName}
        onChangeText={setDisplayName}
        placeholder="设备名称"
        placeholderTextColor={colors.muted}
      />
      <Pressable
        style={styles.primary}
        onPress={async () => {
          if (!permission?.granted) {
            const result = await requestPermission();
            if (!result.granted) {
              setError("需要相机权限才能扫描二维码");
              return;
            }
          }
          setScanning(true);
        }}
      >
        <Text style={styles.primaryText}>扫描配对二维码</Text>
      </Pressable>
      <Text style={styles.or}>或粘贴 pairing_json</Text>
      <TextInput
        style={[styles.input, styles.payload]}
        value={payload}
        onChangeText={setPayload}
        placeholder={'{"version":"v1", ...}'}
        placeholderTextColor={colors.muted}
        multiline
        autoCapitalize="none"
        autoCorrect={false}
      />
      <Pressable style={styles.secondary} onPress={() => void submit()} disabled={working}>
        {working ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.secondaryText}>安全连接</Text>}
      </Pressable>
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24, gap: 16, justifyContent: "center" },
  lead: { color: colors.ink, fontSize: 18, lineHeight: 28, marginBottom: 8 },
  input: { borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, color: colors.ink, borderRadius: 14, padding: 14 },
  payload: { minHeight: 120, textAlignVertical: "top", fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  primary: { backgroundColor: colors.accent, borderRadius: 14, padding: 15, alignItems: "center" },
  primaryText: { color: "white", fontWeight: "700" },
  secondary: { backgroundColor: colors.accentSoft, borderRadius: 14, padding: 15, alignItems: "center" },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  or: { color: colors.muted, textAlign: "center" },
  error: { color: colors.danger, textAlign: "center" },
  scanner: { flex: 1, alignItems: "center", justifyContent: "center" },
  scanFrame: { width: 260, height: 260, borderWidth: 2, borderColor: "white", borderRadius: 24 },
  cancel: { position: "absolute", bottom: 56, backgroundColor: "rgba(0,0,0,0.65)", paddingHorizontal: 20, paddingVertical: 12, borderRadius: 20 },
  cancelText: { color: "white", fontWeight: "600" },
});
