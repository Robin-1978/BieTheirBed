import { CameraView, useCameraPermissions } from "expo-camera";
import * as Linking from "expo-linking";
import { router, useLocalSearchParams } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { colors, radii, spacing, typography } from "@/theme";
import { useI18n } from "@/i18n";
import { prepareImageAttachment } from "@/media/prepareImageAttachment";

export default function CaptureScreen() {
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const insets = useSafeAreaInsets();
  const camera = useRef<CameraView>(null);
  const { t } = useI18n();
  const [permission, requestPermission] = useCameraPermissions();
  const [working, setWorking] = useState(false);
  const [captured, setCaptured] = useState<{ uri: string; name: string } | null>(null);

  async function capture() {
    if (!camera.current || working) return;
    setWorking(true);
    try {
      const photo = await camera.current.takePictureAsync({ quality: 0.65 });
      if (!photo) return;
      const prepared = await prepareImageAttachment(
        photo.uri,
        `camera-${Date.now()}.jpg`,
      );
      setCaptured({ uri: prepared.uri, name: prepared.name });
    } finally {
      setWorking(false);
    }
  }

  function usePhoto() {
    if (!captured) return;
    router.replace({
      pathname: "/(tabs)",
      params: {
        capturedUri: captured.uri,
        capturedName: captured.name,
        workspaceId: stringParam(params.workspaceId),
        workspaceName: stringParam(params.workspaceName),
        nodeId: stringParam(params.nodeId),
      },
    });
  }

  if (!permission?.granted) {
    return (
      <View style={[styles.permission, { paddingTop: insets.top + spacing.large, paddingBottom: insets.bottom + spacing.large }]}>
        <View style={styles.permissionCard}>
          <View style={styles.permissionIconWrap}>
            <AppIcon name="camera" color={colors.accent} size={36} />
          </View>
          <Text style={[styles.permissionTitle, typography.title]}>{t("capture.permission")}</Text>
          <Text style={[styles.permissionHint, typography.caption]}>
            {permission && !permission.canAskAgain
              ? t("chat.microphoneDisabled")
              : t("capture.permission")}
          </Text>
          {permission && !permission.canAskAgain ? (
            <AppPressable style={styles.primaryButton} onPress={() => void Linking.openSettings()}>
              <Text style={styles.primaryButtonText}>{t("pair.openSettings")}</Text>
            </AppPressable>
          ) : (
            <AppPressable style={styles.primaryButton} onPress={() => void requestPermission()}>
              <Text style={styles.primaryButtonText}>{t("capture.allow")}</Text>
            </AppPressable>
          )}
          <AppPressable style={styles.dismissButton} onPress={() => router.back()}>
            <Text style={styles.dismissButtonText}>{t("common.cancel")}</Text>
          </AppPressable>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Top Floating Controls */}
      <View style={[styles.topBar, { top: insets.top + spacing.small }]}>
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={t("common.close")}
          style={styles.circleIconButton}
          onPress={() => router.back()}
        >
          <AppIcon name="x" color="white" size={20} />
        </AppPressable>
      </View>

      {/* Viewfinder */}
      {captured ? (
        <Image resizeMode="contain" source={{ uri: captured.uri }} style={styles.camera} />
      ) : (
        <CameraView ref={camera} style={styles.camera} facing="back" />
      )}

      {/* Bottom Shutter / Action Panel */}
      <View
        style={[
          styles.panel,
          {
            paddingBottom: Math.max(insets.bottom + spacing.small, spacing.xlarge),
          },
        ]}
      >
        {captured ? (
          <View style={styles.previewActions}>
            <AppPressable style={styles.secondaryButton} onPress={() => setCaptured(null)}>
              <AppIcon name="refresh" color={colors.ink} size={18} />
              <Text style={styles.secondaryText}>{t("capture.retake")}</Text>
            </AppPressable>
            <AppPressable style={[styles.primaryButton, styles.flexAction]} onPress={usePhoto}>
              <AppIcon name="check" color={colors.onAccent} size={18} />
              <Text style={styles.primaryButtonText}>{t("capture.use")}</Text>
            </AppPressable>
          </View>
        ) : (
          <View style={styles.shutterRow}>
            <View style={styles.shutterPlaceholder} />
            <AppPressable
              accessibilityRole="button"
              accessibilityLabel={t("capture.take")}
              disabled={working}
              onPress={() => void capture()}
              style={styles.shutterOuter}
            >
              <View style={styles.shutterInner}>
                {working ? <ActivityIndicator color={colors.accent} size="small" /> : null}
              </View>
            </AppPressable>
            <View style={styles.shutterPlaceholder} />
          </View>
        )}
      </View>
    </View>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#000000",
  },
  topBar: {
    position: "absolute",
    left: spacing.large,
    zIndex: 10,
  },
  circleIconButton: {
    width: 44,
    height: 44,
    borderRadius: radii.pill,
    backgroundColor: "rgba(0, 0, 0, 0.45)",
    alignItems: "center",
    justifyContent: "center",
  },
  camera: {
    flex: 1,
  },
  panel: {
    backgroundColor: "#0d1311",
    paddingTop: spacing.large,
    paddingHorizontal: spacing.large,
    borderTopLeftRadius: radii.large,
    borderTopRightRadius: radii.large,
  },
  shutterRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: spacing.small,
  },
  shutterPlaceholder: {
    width: 44,
  },
  shutterOuter: {
    width: 76,
    height: 76,
    borderRadius: 38,
    borderWidth: 4,
    borderColor: "rgba(255, 255, 255, 0.85)",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "transparent",
  },
  shutterInner: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: "white",
    alignItems: "center",
    justifyContent: "center",
  },
  previewActions: {
    flexDirection: "row",
    gap: spacing.medium,
    alignItems: "center",
  },
  secondaryButton: {
    flex: 1,
    flexDirection: "row",
    backgroundColor: colors.surfaceMuted,
    paddingVertical: 14,
    paddingHorizontal: spacing.medium,
    borderRadius: radii.large,
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  secondaryText: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "700",
  },
  primaryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 14,
    paddingHorizontal: spacing.medium,
    borderRadius: radii.large,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
  },
  primaryButtonText: {
    color: colors.onAccent,
    fontSize: 15,
    fontWeight: "700",
  },
  flexAction: {
    flex: 1,
  },
  dismissButton: {
    paddingVertical: spacing.small,
    alignItems: "center",
  },
  dismissButtonText: {
    color: colors.muted,
    fontSize: 14,
  },
  permission: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.large,
  },
  permissionCard: {
    backgroundColor: colors.surface,
    padding: spacing.xlarge,
    borderRadius: radii.large,
    alignItems: "center",
    gap: spacing.medium,
    maxWidth: 340,
    width: "100%",
    borderWidth: 1,
    borderColor: colors.line,
  },
  permissionIconWrap: {
    width: 68,
    height: 68,
    borderRadius: radii.pill,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.small,
  },
  permissionTitle: {
    color: colors.ink,
    textAlign: "center",
  },
  permissionHint: {
    color: colors.muted,
    textAlign: "center",
    lineHeight: 20,
  },
});
