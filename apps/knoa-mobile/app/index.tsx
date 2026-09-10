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
import { colors, radii, spacing, typography } from "@/theme";

type RestoreStage = "connect" | "session" | "nodes";
const STAGE_ORDER: RestoreStage[] = ["connect", "session", "nodes"];

export default function Index() {
  const gateway = useGateway();
  const { t } = useI18n();
  const started = useRef(false);
  const rotation = useRef(new Animated.Value(0)).current;
  const breath = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);
  const [stage, setStage] = useState<RestoreStage>("connect");
  const [failReason, setFailReason] = useState("");

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
    void restoreLanding(gateway, setStage)
      .then((reason) => { if (reason) setFailReason(reason); })
      .catch(() => router.replace("/account"));
  }, [gateway]);

  const failed = gateway.status === "error";
  const retry = () => {
    started.current = false;
    setFailReason("");
    setStage("connect");
    void gateway.reconnect();
  };

  return (
    <View style={styles.container}>
      <View style={styles.coreWrap}>
        <Animated.View style={[styles.orbitOuter, { opacity: breath.interpolate({ inputRange: [0, 1], outputRange: [0.14, 0.4] }), transform: [{ scale: breath.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1.05] }) }] }]} />
        <Animated.View style={[styles.orbitInner, { transform: [{ rotate: rotation.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }] }]}>
          <View style={styles.orbitNode} /><View style={styles.orbitNodeSecondary} />
        </Animated.View>
        <Animated.View style={[styles.coreGlow, { opacity: breath.interpolate({ inputRange: [0, 1], outputRange: [0.22, 0.5] }), transform: [{ scale: breath.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.14] }) }] }]} />
        <View style={styles.core}><Text style={styles.coreText}>诺</Text></View>
      </View>
      <Text style={styles.brand}>小诺</Text>
      <Text style={styles.eyebrow}>KNOA · KNOW-YOU AGENT</Text>
      <Text style={styles.title}>{failed ? t("splash.unavailable") : t("splash.waking")}</Text>
      {failed ? (
        <Text style={styles.detail}>
          {failReason || gateway.error || t("splash.connectionProblem")}
        </Text>
      ) : (
        <View style={styles.stageRow} accessibilityLiveRegion="polite">
          {STAGE_ORDER.map((item) => {
            const active = item === stage;
            const done = STAGE_ORDER.indexOf(item) < STAGE_ORDER.indexOf(stage);
            return (
              <View key={item} style={styles.stageItem}>
                <View style={[styles.stageDot, done && styles.stageDotDone, active && styles.stageDotActive]} />
                <Text style={[styles.stageText, (active || done) && styles.stageTextActive]}>
                  {t(`splash.stage.${item}`)}
                </Text>
              </View>
            );
          })}
        </View>
      )}
      {failed ? (
        <AppPressable onPress={retry} style={styles.retry}>
          <Text style={styles.retryText}>{t("common.reconnect")}</Text>
        </AppPressable>
      ) : null}
    </View>
  );
}

async function restoreLanding(
  gateway: ReturnType<typeof useGateway>,
  reportStage: (stage: RestoreStage) => void,
): Promise<string> {
  reportStage("connect");
  const connection = await loadHubConnection();
  if (!connection) {
    router.replace("/account/login");
    return "";
  }
  reportStage("session");
  // These reads are independent.  Serializing them made the splash screen
  // wait for a slow Hosted Hub request before it could even discover the
  // locally cached Node binding.
  const [preference, bindings, hosted] = await Promise.all([
    loadNavigationPreference(),
    listNodeBindings(),
    connection.accountId
      ? listHostedWorkspaces().catch(() => [] as HostedWorkspace[])
      : Promise.resolve([] as HostedWorkspace[]),
  ]);
  if (preference.landing === "account") {
    router.replace("/account");
    return "";
  }
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
  reportStage("nodes");
  const targetNodeId = preference.nodeId && bindings.some((b) => b.nodeId === preference.nodeId)
    ? preference.nodeId
    : (bindings[0]?.nodeId || gateway.nodeId || "");

  if (targetNodeId) {
    // Do not keep the entire app behind the splash while Relay/P2P is
    // negotiating.  The tabs show the live connection state and can recover
    // in place if the first handshake is slow.
    void gateway.switchNode(targetNodeId).catch(() => undefined);
    router.replace({
      pathname: "/(tabs)",
      params: {
        workspaceId: workspace.workspaceId,
        workspaceName: workspace.displayName,
        nodeId: targetNodeId,
      },
    });
    return "";
  }

  // If no node is bound, guide user directly to pair their computer
  router.replace({
    pathname: "/pair",
    params: {
      workspaceId: workspace.workspaceId,
      workspaceName: workspace.displayName,
    },
  });
  return "";
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.medium, padding: spacing.xlarge, backgroundColor: colors.background },
  coreWrap: { width: 178, height: 178, alignItems: "center", justifyContent: "center", marginBottom: spacing.small },
  orbitOuter: { position: "absolute", width: 168, height: 168, borderRadius: 84, borderWidth: 1, borderColor: colors.accent },
  orbitInner: { position: "absolute", width: 128, height: 128, borderRadius: 64, borderWidth: 1, borderColor: colors.line },
  orbitNode: { position: "absolute", top: -5, left: 58, width: 11, height: 11, borderRadius: 6, backgroundColor: colors.accent },
  orbitNodeSecondary: { position: "absolute", bottom: -3, left: 61, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.accent, opacity: 0.45 },
  coreGlow: { position: "absolute", width: 104, height: 104, borderRadius: 34, backgroundColor: colors.accentSoft },
  core: { width: 92, height: 92, borderRadius: 28, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, shadowColor: colors.accent, shadowOpacity: 0.38, shadowRadius: 22, elevation: 10 },
  coreText: { color: colors.onAccent, fontWeight: "800", fontSize: 40 },
  brand: { color: colors.ink, fontSize: 26 /* typography.brand */, fontWeight: "800", letterSpacing: 4, marginTop: spacing.small },
  eyebrow: { color: colors.accent, fontSize: 11, letterSpacing: 2.6, fontWeight: "700", marginBottom: spacing.small },
  title: { ...typography.subheading, color: colors.ink },
  detail: { ...typography.caption, color: colors.muted, textAlign: "center", lineHeight: 20 },
  stageRow: { flexDirection: "row", alignItems: "center", gap: spacing.large, marginTop: spacing.xsmall },
  stageItem: { flexDirection: "row", alignItems: "center", gap: spacing.xsmall },
  stageDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.lineStrong },
  stageDotDone: { backgroundColor: colors.success },
  stageDotActive: { backgroundColor: colors.accent },
  stageText: { ...typography.tiny, color: colors.muted },
  stageTextActive: { color: colors.ink },
  retry: { paddingHorizontal: spacing.xlarge, paddingVertical: spacing.medium, borderRadius: radii.medium, backgroundColor: colors.accent, marginTop: spacing.small },
  retryText: { color: colors.onAccent, fontWeight: "700" },
});
