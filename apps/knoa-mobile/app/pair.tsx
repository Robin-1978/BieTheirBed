import { CameraView, useCameraPermissions } from "expo-camera";
import * as Linking from "expo-linking";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
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

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { loadConnectionIdentity } from "@/security/deviceIdentity";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";

export default function PairScreen() {
  const gateway = useGateway();
  const params = useLocalSearchParams<{
    workspaceId?: string;
    workspaceName?: string;
    autoScan?: string;
    onboarding?: string;
  }>();
  const { t } = useI18n();
  const [permission, requestPermission] = useCameraPermissions();
  const [displayName, setDisplayName] = useState(() => t("pair.defaultDevice"));
  const [payload, setPayload] = useState("");
  const [scanning, setScanning] = useState(false);
  const [requestingCamera, setRequestingCamera] = useState(false);
  const [working, setWorking] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState("");
  const onboarding = stringParam(params.onboarding) === "1";

  useEffect(() => {
    if (stringParam(params.autoScan) !== "1" || scanning || working) return;
    let active = true;
    void (async () => {
      setRequestingCamera(true);
      try {
        if (!permission?.granted) {
          const result = await requestPermission();
          if (!active || !result.granted) return;
        }
        if (active) setScanning(true);
      } finally {
        if (active) setRequestingCamera(false);
      }
    })();
    return () => { active = false; };
  }, [params.autoScan, permission?.granted, requestPermission, scanning, working]);

  async function submit(encoded = payload) {
    if (!encoded.trim() || !displayName.trim() || working) return;
    setWorking(true);
    setError("");
    try {
      await gateway.pair(encoded.trim(), displayName.trim());
      const identity = await loadConnectionIdentity();
      const workspaceId = stringParam(params.workspaceId);
      if (onboarding && workspaceId) {
        router.replace({
          pathname: "/workspaces/[workspaceId]",
          params: {
            workspaceId,
            workspaceName: stringParam(params.workspaceName),
            connected: "1",
          },
        });
        return;
      }
      router.replace({
        pathname: "/(tabs)",
        params: {
          workspaceId,
          workspaceName: stringParam(params.workspaceName),
          nodeId: identity?.nodeId || "",
        },
      });
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : "";
      setError(/expired|consumed|失效|过期/i.test(detail) ? t("pair.expired") : t("pair.failed"));
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
        <Text style={styles.scanHint}>{working ? t("pair.verifying") : t("pair.align")}</Text>
        <AppPressable style={styles.cancel} onPress={() => setScanning(false)}>
          <Text style={styles.cancelText}>{t("pair.cancelScan")}</Text>
        </AppPressable>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.intro}>
        <View style={styles.introIcon}><AppIcon name="camera" color={colors.accent} size={28} /></View>
        <Text style={styles.title}>{t("pair.title")}</Text>
        <Text style={styles.lead}>{t("pair.lead")}</Text>
        <Text style={styles.expiryHint}>{t("pair.expiryHint")}</Text>
      </View>
      <TextInput
        style={styles.input}
        value={displayName}
        onChangeText={setDisplayName}
        placeholder={t("pair.deviceName")}
        placeholderTextColor={colors.muted}
      />
      <AppPressable
        disabled={requestingCamera || working}
        style={[styles.primary, (requestingCamera || working) && styles.disabled]}
        onPress={async () => {
          setRequestingCamera(true);
          try {
            if (!permission?.granted) {
              const result = await requestPermission();
              if (!result.granted) {
                if (!result.canAskAgain) {
                  setError(t("pair.cameraDisabled"));
                } else {
                  setError(t("pair.cameraRequired"));
                }
                return;
              }
            }
            setScanning(true);
          } finally {
            setRequestingCamera(false);
          }
        }}
      >
        {requestingCamera
          ? <ActivityIndicator color={colors.onAccent} size="small" />
          : <Text style={styles.primaryText}>{t("pair.scan")}</Text>}
      </AppPressable>
      {permission && !permission.granted && !permission.canAskAgain ? (
        <Pressable accessibilityRole="button" onPress={() => void Linking.openSettings()}>
          <Text style={styles.settingsLink}>{t("pair.openSettings")}</Text>
        </Pressable>
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <AppPressable onPress={() => setAdvanced((value) => !value)} style={styles.advancedToggle}>
        <Text style={styles.advancedText}>{advanced ? t("pair.hideAdvanced") : t("pair.showAdvanced")}</Text>
        <AppIcon name="chevron-right" color={colors.muted} size={17} />
      </AppPressable>
      {advanced ? (
        <View style={styles.advancedCard}>
          <Text style={styles.command}>{t("pair.advancedHint")}</Text>
          <Text style={styles.or}>{t("pair.orPaste")}</Text>
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
          <AppPressable style={styles.secondary} onPress={() => void submit()} disabled={working || !payload.trim()}>
            {working ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.secondaryText}>{t("pair.connect")}</Text>}
          </AppPressable>
        </View>
      ) : null}
    </KeyboardAvoidingView>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: spacing.xlarge, gap: spacing.large, justifyContent: "center" },
  intro: { alignItems: "center", gap: spacing.small, marginBottom: spacing.small },
  introIcon: { width: 58, height: 58, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  title: { color: colors.ink, fontSize: 23, fontWeight: "700" },
  lead: { color: colors.ink, fontSize: 16, lineHeight: 24, textAlign: "center" },
  expiryHint: { color: colors.muted, fontSize: 13, lineHeight: 20, textAlign: "center" },
  input: { borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, color: colors.ink, borderRadius: radii.medium, padding: spacing.large },
  payload: { minHeight: 120, textAlignVertical: "top", fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  primary: { backgroundColor: colors.accent, borderRadius: radii.medium, padding: spacing.large, alignItems: "center" },
  primaryText: { color: colors.onAccent, fontWeight: "700" },
  secondary: { backgroundColor: colors.accentSoft, borderRadius: radii.medium, padding: spacing.large, alignItems: "center" },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  or: { color: colors.muted, textAlign: "center" },
  error: { color: colors.danger, textAlign: "center" },
  disabled: { opacity: 0.5 },
  settingsLink: { color: colors.accent, textAlign: "center", fontWeight: "700" },
  scanner: { flex: 1, alignItems: "center", justifyContent: "center" },
  scanFrame: { width: 260, height: 260, borderWidth: 2, borderColor: "white", borderRadius: 24 },
  scanHint: { position: "absolute", bottom: 116, color: "white", backgroundColor: "rgba(0,0,0,0.58)", paddingHorizontal: spacing.large, paddingVertical: spacing.small, borderRadius: radii.large },
  cancel: { position: "absolute", bottom: 56, backgroundColor: "rgba(0,0,0,0.65)", paddingHorizontal: spacing.xlarge, paddingVertical: spacing.medium, borderRadius: radii.large },
  cancelText: { color: "white", fontWeight: "600" },
  advancedToggle: { minHeight: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small },
  advancedText: { color: colors.muted, fontWeight: "600" },
  advancedCard: { gap: spacing.medium, padding: spacing.large, borderRadius: radii.medium, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  command: { color: colors.ink, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", fontSize: 13, backgroundColor: colors.background, borderRadius: radii.small, padding: spacing.medium },
});
