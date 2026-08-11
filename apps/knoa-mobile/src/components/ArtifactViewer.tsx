import * as Linking from "expo-linking";
import * as MediaLibrary from "expo-media-library";
import * as Sharing from "expo-sharing";
import { useCallback, useState } from "react";
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { ResolvedArtifactFile } from "@/api/chatArtifacts";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

export function ArtifactViewer({
  file,
  onClose,
  onMessage,
}: {
  file: ResolvedArtifactFile | null;
  onClose(): void;
  onMessage(message: string, tone?: "success" | "error" | "info"): void;
}) {
  const { t } = useI18n();
  const insets = useSafeAreaInsets();
  const [working, setWorking] = useState<"save" | "share" | "">("");
  const scale = useSharedValue(1);
  const savedScale = useSharedValue(1);
  const translateX = useSharedValue(0);
  const translateY = useSharedValue(0);
  const savedTranslateX = useSharedValue(0);
  const savedTranslateY = useSharedValue(0);

  const reset = useCallback(() => {
    "worklet";
    scale.value = withTiming(1);
    savedScale.value = 1;
    translateX.value = withTiming(0);
    translateY.value = withTiming(0);
    savedTranslateX.value = 0;
    savedTranslateY.value = 0;
  }, [savedScale, savedTranslateX, savedTranslateY, scale, translateX, translateY]);

  const pinch = Gesture.Pinch()
    .onUpdate((event) => { scale.value = Math.min(5, Math.max(1, savedScale.value * event.scale)); })
    .onEnd(() => {
      savedScale.value = scale.value;
      if (scale.value <= 1.05) reset();
    });
  const pan = Gesture.Pan()
    .onUpdate((event) => {
      if (scale.value <= 1) return;
      translateX.value = savedTranslateX.value + event.translationX;
      translateY.value = savedTranslateY.value + event.translationY;
    })
    .onEnd(() => {
      savedTranslateX.value = translateX.value;
      savedTranslateY.value = translateY.value;
    });
  const doubleTap = Gesture.Tap().numberOfTaps(2).maxDuration(280).onEnd(() => {
    if (scale.value > 1) reset();
    else {
      scale.value = withTiming(2.5);
      savedScale.value = 2.5;
    }
  });
  const gesture = Gesture.Race(doubleTap, Gesture.Simultaneous(pinch, pan));
  const imageStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: translateX.value },
      { translateY: translateY.value },
      { scale: scale.value },
    ],
  }));

  async function save() {
    if (!file || working) return;
    setWorking("save");
    try {
      const permission = await MediaLibrary.requestPermissionsAsync(true, ["photo"]);
      if (!permission.granted) {
        onMessage(permission.canAskAgain
          ? t("artifact.photoPermission")
          : t("artifact.photoPermissionDisabled"), "error");
        if (!permission.canAskAgain) await Linking.openSettings();
        return;
      }
      await MediaLibrary.createAssetAsync(file.uri);
      onMessage(t("artifact.savedToPhotos"), "success");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : t("artifact.saveImageFailed"), "error");
    } finally {
      setWorking("");
    }
  }

  async function share() {
    if (!file || working) return;
    setWorking("share");
    try {
      await Sharing.shareAsync(file.uri, { mimeType: file.mediaType });
    } catch (error) {
      onMessage(error instanceof Error ? error.message : t("artifact.shareFailed"), "error");
    } finally {
      setWorking("");
    }
  }

  return (
    <Modal animationType="fade" onRequestClose={onClose} transparent visible={Boolean(file)}>
      {file ? (
        <GestureHandlerRootView style={styles.root}>
          <View style={[styles.toolbar, { paddingTop: insets.top + 6 }]}>
            <Pressable accessibilityLabel={t("artifact.closePreview")} onPress={onClose} style={styles.button}>
              <Text style={styles.buttonText}>{t("artifact.close")}</Text>
            </Pressable>
            <Text numberOfLines={1} style={styles.name}>{file.name}</Text>
            <View style={styles.actions}>
              <Pressable accessibilityLabel={t("artifact.saveToPhotos")} disabled={Boolean(working)} onPress={() => void save()} style={styles.button}>
                {working === "save" ? <ActivityIndicator color="white" size="small" /> : <Text style={styles.buttonText}>{t("artifact.save")}</Text>}
              </Pressable>
              <Pressable accessibilityLabel={t("artifact.shareImage")} disabled={Boolean(working)} onPress={() => void share()} style={styles.button}>
                {working === "share" ? <ActivityIndicator color="white" size="small" /> : <Text style={styles.buttonText}>{t("artifact.share")}</Text>}
              </Pressable>
            </View>
          </View>
          <GestureDetector gesture={gesture}>
            <Animated.View style={styles.canvas}>
              <Animated.Image resizeMode="contain" source={{ uri: file.uri }} style={[styles.image, imageStyle]} />
            </Animated.View>
          </GestureDetector>
          <Text style={[styles.hint, { bottom: insets.bottom + 16 }]}>{t("artifact.zoomHint")}</Text>
        </GestureHandlerRootView>
      ) : null}
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#050706" },
  toolbar: { minHeight: 58, paddingBottom: 10, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "rgba(5,7,6,0.92)", zIndex: 2 },
  button: { minWidth: 48, minHeight: 38, paddingHorizontal: 10, justifyContent: "center", alignItems: "center", borderRadius: 10, backgroundColor: "rgba(255,255,255,0.12)" },
  buttonText: { color: "white", fontWeight: "700" },
  name: { color: "white", flex: 1, textAlign: "center", fontWeight: "600" },
  actions: { flexDirection: "row", gap: 6 },
  canvas: { flex: 1, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  image: { width: "100%", height: "100%" },
  hint: { position: "absolute", alignSelf: "center", color: colors.line, fontSize: 12, backgroundColor: "rgba(0,0,0,0.45)", paddingHorizontal: 12, paddingVertical: 7, borderRadius: 14 },
});
