import { router } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, StyleSheet, Text, View } from "react-native";
import { AppPressable } from "@/components/AppPressable";

import {
  listHostedWorkspaces,
  loadHubConnection,
  selectHostedWorkspace,
  type HostedWorkspace,
} from "@/hub/hubClient";
import { loadNavigationPreference } from "@/navigation/navigationPreference";
import { listNodeBindings } from "@/security/deviceIdentity";
import { useGateway } from "@/state/GatewayProvider";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, shadows, typography } from "@/theme";

export default function Index() {
  const gateway = useGateway();
  const { t } = useI18n();
  const started = useRef(false);
  const rotation = useRef(new Animated.Value(0)).current;
  const breath = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    let active = true;
    void AccessibilityInfo.isReduceMotionEnabled().then((enabled) => { if (active) setReduceMotion(enabled); });
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => { active = false; subscription.remove(); };
  }, []);

  useEffect(() => {
    if (reduceMotion) return;
    const orbitAnimation = Animated.loop(Animated.timing(rotation, { toValue: 1, duration: 5200, easing: Easing.linear, useNativeDriver: true }));
    const breathAnimation = Animated.loop(Animated.sequence([
      Animated.timing(breath, { toValue: 1, duration: 1400, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      Animated.timing(breath, { toValue: 0, duration: 1400, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
    ]));
    orbitAnimation.start(); breathAnimation.start();
    return () => { orbitAnimation.stop(); breathAnimation.stop(); };
  }, [breath, reduceMotion, rotation]);

  useEffect(() => {
    if (gateway.status === "booting" || started.current) return;
    started.current = true;
    void restoreLanding(gateway).catch(() => router.replace("/account"));
  }, [gateway]);

  const failed = gateway.status === "error";
  const retry = () => {
    started.current = false;
    void gateway.reconnect();
  };

  return (
    <View style={styles.container}>
      <View style={styles.coreWrap}>
        <Animated.View style={[styles.orbitOuter, { opacity: breath.interpolate({ inputRange: [0, 1], outputRange: [0.18, 0.52] }), transform: [{ scale: breath.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1.05] }) }] }]} />
        <Animated.View style={[styles.orbitInner, { transform: [{ rotate: rotation.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }] }]}>
          <View style={styles.orbitNode} /><View style={styles.orbitNodeSecondary} />
        </Animated.View>
        <Animated.View style={[styles.coreGlow, { opacity: breath.interpolate({ inputRange: [0, 1], outputRange: [0.22, 0.48] }), transform: [{ scale: breath.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.12] }) }] }]} />
        <View style={styles.core}><Text style={styles.coreText}>诺</Text></View>
      </View>
      <Text style={styles.eyebrow}>KNOA</Text>
      <Text style={styles.title}>{failed ? t("splash.unavailable") : t("boot.restoring")}</Text>
      <Text style={styles.detail}>{failed ? (gateway.error || t("splash.connectionProblem")) : t("splash.restoring")}</Text>
      {failed ? (
        <AppPressable onPress={retry} style={styles.retry}>
          <Text style={styles.retryText}>{t("common.reconnect")}</Text>
        </AppPressable>
      ) : null}
    </View>
  );
}

async function restoreLanding(gateway: ReturnType<typeof useGateway>): Promise<void> {
  const connection = await loadHubConnection();
  if (!connection) {
    router.replace("/account/login");
    return;
  }
  const preference = await loadNavigationPreference();
  if (preference.landing === "account") {
    router.replace("/account");
    return;
  }
  const hosted = connection.accountId ? await listHostedWorkspaces() : [];
  const fallback: HostedWorkspace = {
    workspaceId: connection.workspaceId,
    displayName: preference.workspaceName || "Personal Workspace",
    kind: "personal",
    role: "owner",
    workspacePath: "",
  };
  const workspace = hosted.find((item) => item.workspaceId === preference.workspaceId)
    ?? hosted.find((item) => item.workspaceId === connection.workspaceId)
    ?? fallback;
  if (connection.accountId && workspace.workspaceId !== connection.workspaceId) {
    await selectHostedWorkspace(workspace);
  }
  const bindings = await listNodeBindings();
  const targetNodeId = preference.nodeId && bindings.some((b) => b.nodeId === preference.nodeId)
    ? preference.nodeId
    : (bindings[0]?.nodeId || gateway.nodeId || "");

  if (targetNodeId) {
    try {
      await gateway.switchNode(targetNodeId);
    } catch {
      // ignore switch error, still proceed to tabs
    }
    router.replace({
      pathname: "/(tabs)",
      params: {
        workspaceId: workspace.workspaceId,
        workspaceName: workspace.displayName,
        nodeId: targetNodeId,
      },
    });
    return;
  }

  // If no node is bound, guide user directly to pair their computer
  router.replace({
    pathname: "/pair",
    params: {
      workspaceId: workspace.workspaceId,
      workspaceName: workspace.displayName,
    },
  });
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.large, padding: spacing.xlarge, backgroundColor: colors.background },
  coreWrap: { width: 154, height: 154, alignItems: "center", justifyContent: "center", marginBottom: spacing.small },
  orbitOuter: { position: "absolute", width: 146, height: 146, borderRadius: 73, borderWidth: 1, borderColor: colors.accent },
  orbitInner: { position: "absolute", width: 112, height: 112, borderRadius: 56, borderWidth: 1, borderColor: colors.line },
  orbitNode: { position: "absolute", top: -5, left: 50, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent },
  orbitNodeSecondary: { position: "absolute", bottom: -3, left: 53, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent, opacity: 0.45 },
  coreGlow: { position: "absolute", width: 88, height: 88, borderRadius: 30, backgroundColor: colors.accentSoft },
  core: { width: 76, height: 76, borderRadius: 24, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.32, shadowRadius: 18, elevation: 8 },
  coreText: { color: colors.onAccent, fontWeight: "800", fontSize: 31 },
  eyebrow: { color: colors.accent, fontSize: 11, letterSpacing: 2.2, fontWeight: "700" },
  title: { color: colors.ink, fontSize: 18, fontWeight: "700" },
  detail: { color: colors.muted, fontSize: 13, textAlign: "center", lineHeight: 20 },
  retry: { paddingHorizontal: spacing.xlarge, paddingVertical: spacing.medium, borderRadius: radii.medium, backgroundColor: colors.accent },
  retryText: { color: colors.onAccent, fontWeight: "800" },
});
