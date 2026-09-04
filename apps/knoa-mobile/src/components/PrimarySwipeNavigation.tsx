import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState, type PropsWithChildren } from "react";
import { StyleSheet, useWindowDimensions } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  runOnJS,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
} from "react-native-reanimated";

export type PrimaryScreen = "chat" | "tasks";

let pendingEntryDirection: -1 | 0 | 1 = 0;

type NodeRouteParams = {
  workspaceId?: string;
  workspaceName?: string;
  nodeId?: string;
};

export function navigatePrimary(current: PrimaryScreen, target: PrimaryScreen, params: NodeRouteParams = {}) {
  if (current === target) return;
  pendingEntryDirection = target === "tasks" ? 1 : -1;
  router.replace({ pathname: target === "tasks" ? "/(tabs)/tasks" : "/(tabs)", params });
}

function consumeEntryDirection(): -1 | 0 | 1 {
  const direction = pendingEntryDirection;
  pendingEntryDirection = 0;
  return direction;
}

export function PrimarySwipeNavigation({ current, children }: PropsWithChildren<{ current: PrimaryScreen }>) {
  const routeParams = useLocalSearchParams<NodeRouteParams>();
  const { width } = useWindowDimensions();
  const reduceMotion = useReducedMotion();
  const [entryDirection] = useState(consumeEntryDirection);
  const translateX = useSharedValue(entryDirection * Math.min(width * 0.14, 52));

  useEffect(() => {
    translateX.value = withTiming(0, { duration: reduceMotion ? 0 : 180 });
  }, [reduceMotion, translateX]);

  const finishNavigation = (target: PrimaryScreen) => navigatePrimary(current, target, {
    workspaceId: stringParam(routeParams.workspaceId),
    workspaceName: stringParam(routeParams.workspaceName),
    nodeId: stringParam(routeParams.nodeId),
  });
  const gesture = Gesture.Pan()
    .activeOffsetX([-18, 18])
    .failOffsetY([-24, 24])
    .onUpdate((event) => {
      const allowed = (current === "chat" && event.translationX < 0)
        || (current === "tasks" && event.translationX > 0);
      translateX.value = allowed ? event.translationX : event.translationX * 0.08;
    })
    .onEnd((event) => {
      const deliberate = Math.abs(event.translationX) >= 72 || Math.abs(event.velocityX) >= 650;
      const target = current === "chat" && event.translationX < 0
        ? "tasks"
        : current === "tasks" && event.translationX > 0
          ? "chat"
          : null;
      if (deliberate && target) {
        if (reduceMotion) {
          runOnJS(finishNavigation)(target);
          return;
        }
        const settle = target === "tasks" ? -Math.min(width * 0.18, 64) : Math.min(width * 0.18, 64);
        translateX.value = withTiming(settle, { duration: 90 }, (finished) => {
          if (finished) runOnJS(finishNavigation)(target);
        });
        return;
      }
      translateX.value = withSpring(0, { damping: 22, stiffness: 240 });
    });

  const animatedStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: translateX.value }],
  }));

  return (
    <GestureDetector gesture={gesture}>
      <Animated.View style={[styles.root, animatedStyle]}>{children}</Animated.View>
    </GestureDetector>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({ root: { flex: 1 } });
