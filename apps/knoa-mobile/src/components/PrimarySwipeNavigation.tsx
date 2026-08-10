import { router } from "expo-router";
import type { PropsWithChildren } from "react";
import { StyleSheet } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated from "react-native-reanimated";

export function PrimarySwipeNavigation({ current, children }: PropsWithChildren<{ current: "chat" | "tasks" }>) {
  const gesture = Gesture.Pan()
    .activeOffsetX([-42, 42])
    .failOffsetY([-20, 20])
    .runOnJS(true)
    .onEnd((event) => {
      const deliberate = Math.abs(event.translationX) >= 72 || Math.abs(event.velocityX) >= 650;
      if (!deliberate) return;
      if (current === "chat" && event.translationX < 0) router.replace("/tasks");
      if (current === "tasks" && event.translationX > 0) router.replace("/chat");
    });

  return (
    <GestureDetector gesture={gesture}>
      <Animated.View style={styles.root}>{children}</Animated.View>
    </GestureDetector>
  );
}

const styles = StyleSheet.create({ root: { flex: 1 } });
