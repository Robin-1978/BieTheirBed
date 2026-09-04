import { CameraView, useCameraPermissions } from "expo-camera";
import * as Linking from "expo-linking";
import { router, useLocalSearchParams } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { colors, radii, spacing, shadows, typography } from "@/theme";
import { useI18n } from "@/i18n";
import { prepareImageAttachment } from "@/media/prepareImageAttachment";

export default function CaptureScreen() {
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
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
      <View style={styles.permission}>
        <Text style={styles.permissionText}>{t("capture.permission")}</Text>
        {permission && !permission.canAskAgain ? (
          <Pressable style={styles.button} onPress={() => void Linking.openSettings()}>
            <Text style={styles.buttonText}>{t("pair.openSettings")}</Text>
          </Pressable>
        ) : (
          <Pressable style={styles.button} onPress={() => void requestPermission()}>
            <Text style={styles.buttonText}>{t("capture.allow")}</Text>
          </Pressable>
        )}
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {captured ? (
        <Image resizeMode="contain" source={{ uri: captured.uri }} style={styles.camera} />
      ) : (
        <CameraView ref={camera} style={styles.camera} facing="back" />
      )}
      <View style={styles.panel}>
        {captured ? (
          <View style={styles.previewActions}>
            <Pressable style={styles.secondaryButton} onPress={() => setCaptured(null)}>
              <Text style={styles.secondaryText}>{t("capture.retake")}</Text>
            </Pressable>
            <Pressable style={[styles.button, styles.flexAction]} onPress={usePhoto}>
              <Text style={styles.buttonText}>{t("capture.use")}</Text>
            </Pressable>
          </View>
        ) : (
          <Pressable style={styles.button} onPress={() => void capture()} disabled={working}>
            {working ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>{t("capture.take")}</Text>}
          </Pressable>
        )}
      </View>
    </View>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "black" },
  camera: { flex: 1 },
  panel: { backgroundColor: colors.background, padding: spacing.large, gap: spacing.medium },
  buttonText: { color: "white", fontWeight: "700" },
  previewActions: { flexDirection: "row", gap: spacing.medium },
  secondaryButton: { flex: 1, backgroundColor: colors.accentSoft, padding: spacing.large, borderRadius: radii.medium, alignItems: "center" },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  button: { backgroundColor: colors.accent, padding: spacing.large, borderRadius: radii.medium, alignItems: "center" },
  flexAction: { flex: 1 },
  permission: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xlarge, gap: spacing.large },
  permissionText: { color: colors.ink, fontSize: 18 },
});
