import { CameraView, useCameraPermissions } from "expo-camera";
import * as Linking from "expo-linking";
import { router } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { colors } from "@/theme";
import { useI18n } from "@/i18n";

export default function CaptureScreen() {
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
      setCaptured({ uri: photo.uri, name: `camera-${Date.now()}.jpg` });
    } finally {
      setWorking(false);
    }
  }

  function usePhoto() {
    if (!captured) return;
    router.replace({
        pathname: "/chat",
        params: {
          capturedUri: captured.uri,
          capturedName: captured.name,
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

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "black" },
  camera: { flex: 1 },
  panel: { backgroundColor: colors.background, padding: 16, gap: 12 },
  buttonText: { color: "white", fontWeight: "700" },
  previewActions: { flexDirection: "row", gap: 12 },
  secondaryButton: { flex: 1, backgroundColor: colors.accentSoft, padding: 14, borderRadius: 14, alignItems: "center" },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  button: { backgroundColor: colors.accent, padding: 14, borderRadius: 14, alignItems: "center" },
  flexAction: { flex: 1 },
  permission: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  permissionText: { color: colors.ink, fontSize: 18 },
});
