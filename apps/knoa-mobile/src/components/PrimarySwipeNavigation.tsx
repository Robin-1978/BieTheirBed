import { router } from "expo-router";
import { useEffect, useState, type PropsWithChildren } from "react";
import { StyleSheet, useWindowDimensions } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from "react-native-reanimated";

export type PrimaryScreen = "chat" | "tasks";

let pendingEntryDirection: -1 | 0 | 1 = 0;

export function navigatePrimary(current: PrimaryScreen, target: PrimaryScreen) {
  if (current === target) return;
  pendingEntryDirection = target === "tasks" ? 1 : -1;
  router.replace(target === "tasks" ? "/tasks" : "/chat");
}

function consumeEntryDirection(): -1 | 0 | 1 {
  const direction = pendingEntryDirection;
  pendingEntryDirection = 0;
  return direction;
}

export function PrimarySwipeNavigation({ current, children }: PropsWithChildren<{ current: PrimaryScreen }>) {
  const { width } = useWindowDimensions();
  const [entryDirection] = useState(consumeEntryDirection);
  const translateX = useSharedValue(entryDirection * Math.min(width * 0.22, 88));
  const opacity = useSharedValue(entryDirection ? 0.72 : 1);

  useEffect(() => {
    translateX.value = withTiming(0, { duration: 220 });
    opacity.value = withTiming(1, { duration: 180 });
  }, [opacity, translateX]);

  const finishNavigation = (target: PrimaryScreen) => navigatePrimary(current, target);
  const gesture = Gesture.Pan()
    .activeOffsetX([-18, 18])
    .failOffsetY([-24, 24])
    .onUpdate((event) => {
      const allowed = (current === "chat" && event.translationX < 0)
        || (current === "tasks" && event.translationX > 0);
      translateX.value = allowed ? event.translationX : event.translationX * 0.08;
      opacity.value = allowed ? Math.max(0.82, 1 - Math.abs(event.translationX) / (width * 1.8)) : 1;
    })
    .onEnd((event) => {
      const deliberate = Math.abs(event.translationX) >= 72 || Math.abs(event.velocityX) >= 650;
      const target = current === "chat" && event.translationX < 0
        ? "tasks"
        : current === "tasks" && event.translationX > 0
          ? "chat"
          : null;
      if (deliberate && target) {
        const exit = target === "tasks" ? -width : width;
        opacity.value = withTiming(0.72, { duration: 170 });
        translateX.value = withTiming(exit, { duration: 180 }, (finished) => {
          if (finished) runOnJS(finishNavigation)(target);
        });
        return;
      }
      opacity.value = withTiming(1, { duration: 120 });
      translateX.value = withSpring(0, { damping: 22, stiffness: 240 });
    });

  const animatedStyle = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ translateX: translateX.value }],
  }));

  return (
    <GestureDetector gesture={gesture}>
      <Animated.View style={[styles.root, animatedStyle]}>{children}</Animated.View>
    </GestureDetector>
  );
}

const styles = StyleSheet.create({ root: { flex: 1 } });
