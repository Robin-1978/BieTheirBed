import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { colors } from "@/theme";

export default function CaptureScreen() {
  const camera = useRef<CameraView>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [working, setWorking] = useState(false);

  async function capture() {
    if (!camera.current || working) return;
    setWorking(true);
    try {
      const photo = await camera.current.takePictureAsync({ quality: 0.65 });
      if (!photo) return;
      router.replace({
        pathname: "/chat",
        params: {
          capturedUri: photo.uri,
          capturedName: `camera-${Date.now()}.jpg`,
        },
      });
    } finally {
      setWorking(false);
    }
  }

  if (!permission?.granted) {
    return (
      <View style={styles.permission}>
        <Text style={styles.permissionText}>需要相机权限才能拍照</Text>
        <Pressable style={styles.button} onPress={() => void requestPermission()}>
          <Text style={styles.buttonText}>允许相机</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <CameraView ref={camera} style={styles.camera} facing="back" />
      <View style={styles.panel}>
        <Pressable style={styles.button} onPress={() => void capture()} disabled={working}>
          {working ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>拍照</Text>}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "black" },
  camera: { flex: 1 },
  panel: { backgroundColor: colors.background, padding: 16, gap: 12 },
  button: { backgroundColor: colors.accent, padding: 14, borderRadius: 14, alignItems: "center" },
  buttonText: { color: "white", fontWeight: "700" },
  permission: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  permissionText: { color: colors.ink, fontSize: 18 },
});
