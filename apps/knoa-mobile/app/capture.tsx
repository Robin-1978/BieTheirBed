import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function CaptureScreen() {
  const gateway = useGateway();
  const camera = useRef<CameraView>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [prompt, setPrompt] = useState("请分析这张照片");
  const [working, setWorking] = useState(false);

  async function capture() {
    if (!camera.current || !gateway.client || working) return;
    setWorking(true);
    try {
      const photo = await camera.current.takePictureAsync({ quality: 0.85 });
      if (!photo) return;
      const response = await fetch(photo.uri);
      const artifact = await gateway.client.uploadArtifact({
        sessionHandle: gateway.sessionHandle,
        bytes: await response.arrayBuffer(),
        mediaType: "image/jpeg",
        name: `camera-${Date.now()}.jpg`,
        caption: "手机拍照",
      });
      const accepted = await gateway.client.createTask({
        sessionHandle: gateway.sessionHandle,
        text: prompt.trim(),
        attachments: [artifact],
      });
      router.replace(`/tasks/${accepted.task_id}`);
    } finally {
      setWorking(false);
    }
  }

  if (!permission?.granted) {
    return (
      <View style={styles.permission}>
        <Text style={styles.permissionText}>拍照任务需要相机权限</Text>
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
        <TextInput
          value={prompt}
          onChangeText={setPrompt}
          style={styles.input}
          placeholder="告诉小诺要看什么"
          placeholderTextColor={colors.muted}
        />
        <Pressable style={styles.button} onPress={() => void capture()} disabled={working}>
          {working ? <ActivityIndicator color="white" /> : <Text style={styles.buttonText}>拍照并创建任务</Text>}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "black" },
  camera: { flex: 1 },
  panel: { backgroundColor: colors.background, padding: 16, gap: 12 },
  input: { color: colors.ink, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, borderRadius: 14, padding: 13 },
  button: { backgroundColor: colors.accent, padding: 14, borderRadius: 14, alignItems: "center" },
  buttonText: { color: "white", fontWeight: "700" },
  permission: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  permissionText: { color: colors.ink, fontSize: 18 },
});
