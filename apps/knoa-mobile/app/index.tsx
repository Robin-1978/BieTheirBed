import { Redirect } from "expo-router";
import { useEffect, useRef } from "react";
import { Animated, Easing, StyleSheet, Text, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function Index() {
  const gateway = useGateway();
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const animation = Animated.loop(Animated.timing(pulse, {
      toValue: 1,
      duration: 2200,
      easing: Easing.inOut(Easing.ease),
      useNativeDriver: true,
    }));
    animation.start();
    return () => animation.stop();
  }, [pulse]);
  if (gateway.status === "unpaired") return <Redirect href="/pair" />;
  if (gateway.status === "ready") return <Redirect href="/chat" />;
  const booting = gateway.status === "booting";
  return (
    <View style={styles.container}>
      <View style={styles.coreWrap}>
        <Animated.View style={[styles.orbitOuter, {
          opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.2, 0.7] }),
          transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.08] }) }],
        }]} />
        <Animated.View style={[styles.orbitInner, {
          transform: [{ rotate: pulse.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }],
        }]}>
          <View style={styles.orbitNode} />
        </Animated.View>
        <View style={styles.core}><Text style={styles.coreText}>诺</Text></View>
      </View>
      <Text style={styles.eyebrow}>KNOA SECURE LINK</Text>
      <Text style={styles.title}>{booting ? "正在唤醒小诺" : "暂时连接不上小诺"}</Text>
      {booting ? (
        <View style={styles.statusRow}>
          <Text style={styles.status}>安全连接</Text><View style={styles.dot} />
          <Text style={styles.status}>身份校验</Text><View style={styles.dot} />
          <Text style={styles.status}>会话恢复</Text>
        </View>
      ) : null}
      {gateway.error ? <Text style={styles.detail}>{gateway.error}</Text> : null}
      {gateway.status === "error" ? (
        <AppPressable style={styles.button} onPress={() => void gateway.reconnect()}>
          <Text style={styles.buttonText}>重新连接</Text>
        </AppPressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 28, backgroundColor: colors.background },
  coreWrap: { width: 154, height: 154, alignItems: "center", justifyContent: "center", marginBottom: 8 },
  orbitOuter: { position: "absolute", width: 146, height: 146, borderRadius: 73, borderWidth: 1, borderColor: colors.accent },
  orbitInner: { position: "absolute", width: 112, height: 112, borderRadius: 56, borderWidth: 1, borderColor: colors.line },
  orbitNode: { position: "absolute", top: -5, left: 50, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent },
  core: { width: 76, height: 76, borderRadius: 24, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.32, shadowRadius: 18, elevation: 8 },
  coreText: { color: "white", fontWeight: "800", fontSize: 31 },
  eyebrow: { color: colors.accent, fontSize: 11, letterSpacing: 2.2, fontWeight: "700" },
  title: { color: colors.ink, fontSize: 24, fontWeight: "700" },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  status: { color: colors.muted, fontSize: 12 },
  dot: { width: 3, height: 3, borderRadius: 2, backgroundColor: colors.accent },
  detail: { color: colors.muted, textAlign: "center", maxWidth: 300 },
  button: { backgroundColor: colors.accent, paddingHorizontal: 22, paddingVertical: 12, borderRadius: 14 },
  buttonText: { color: "white", fontWeight: "600" },
});
