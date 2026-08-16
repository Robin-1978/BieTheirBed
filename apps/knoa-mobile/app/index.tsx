import { Redirect } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, StyleSheet, Text, View } from "react-native";

import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function Index() {
  const gateway = useGateway();
  const { t } = useI18n();
  const rotation = useRef(new Animated.Value(0)).current;
  const breath = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    let active = true;
    void AccessibilityInfo.isReduceMotionEnabled().then((enabled) => {
      if (active) setReduceMotion(enabled);
    });
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => {
      active = false;
      subscription.remove();
    };
  }, []);
  useEffect(() => {
    if (reduceMotion) return;
    const orbitAnimation = Animated.loop(Animated.timing(rotation, {
      toValue: 1,
      duration: 5200,
      easing: Easing.linear,
      useNativeDriver: true,
    }));
    const breathAnimation = Animated.loop(Animated.sequence([
      Animated.timing(breath, {
        toValue: 1,
        duration: 1400,
        easing: Easing.inOut(Easing.ease),
        useNativeDriver: true,
      }),
      Animated.timing(breath, {
        toValue: 0,
        duration: 1400,
        easing: Easing.inOut(Easing.ease),
        useNativeDriver: true,
      }),
    ]));
    orbitAnimation.start();
    breathAnimation.start();
    return () => {
      orbitAnimation.stop();
      breathAnimation.stop();
    };
  }, [breath, reduceMotion, rotation]);
  if (gateway.status === "selecting" || gateway.status === "unpaired") return <Redirect href="/connect" />;
  if (gateway.status === "ready") return <Redirect href="/chat" />;
  if (gateway.status === "error") return <Redirect href="/connect" />;
  return (
    <View style={styles.container}>
      <View style={styles.coreWrap}>
        <Animated.View style={[styles.orbitOuter, {
          opacity: breath.interpolate({ inputRange: [0, 1], outputRange: [0.18, 0.52] }),
          transform: [{ scale: breath.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1.05] }) }],
        }]} />
        <Animated.View style={[styles.orbitInner, {
          transform: [{ rotate: rotation.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }],
        }]}>
          <View style={styles.orbitNode} />
          <View style={styles.orbitNodeSecondary} />
        </Animated.View>
        <Animated.View style={[styles.coreGlow, {
          opacity: breath.interpolate({ inputRange: [0, 1], outputRange: [0.22, 0.48] }),
          transform: [{ scale: breath.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.12] }) }],
        }]} />
        <View style={styles.core}><Text style={styles.coreText}>诺</Text></View>
      </View>
      <Text style={styles.eyebrow}>KNOA</Text>
      <Text style={styles.title}>{t("splash.waking")}</Text>
      <Text style={styles.status}>{t("splash.restoring")}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 28, backgroundColor: colors.background },
  coreWrap: { width: 154, height: 154, alignItems: "center", justifyContent: "center", marginBottom: 8 },
  orbitOuter: { position: "absolute", width: 146, height: 146, borderRadius: 73, borderWidth: 1, borderColor: colors.accent },
  orbitInner: { position: "absolute", width: 112, height: 112, borderRadius: 56, borderWidth: 1, borderColor: colors.line },
  orbitNode: { position: "absolute", top: -5, left: 50, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent },
  orbitNodeSecondary: { position: "absolute", bottom: -3, left: 53, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent, opacity: 0.45 },
  coreGlow: { position: "absolute", width: 88, height: 88, borderRadius: 30, backgroundColor: colors.accentSoft },
  core: { width: 76, height: 76, borderRadius: 24, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.32, shadowRadius: 18, elevation: 8 },
  coreText: { color: "white", fontWeight: "800", fontSize: 31 },
  eyebrow: { color: colors.accent, fontSize: 11, letterSpacing: 2.2, fontWeight: "700" },
  title: { color: colors.ink, fontSize: 24, fontWeight: "700" },
  status: { color: colors.muted, fontSize: 13 },
});
