import { router } from "expo-router";
import { useEffect, useRef } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
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
import { colors } from "@/theme";

export default function Index() {
  const gateway = useGateway();
  const { t } = useI18n();
  const started = useRef(false);

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
      <View style={styles.mark}><Text style={styles.markText}>诺</Text></View>
      <Text style={styles.title}>{failed ? t("splash.unavailable") : t("boot.restoring")}</Text>
      <Text style={styles.detail}>{failed ? (gateway.error || t("splash.connectionProblem")) : t("splash.restoring")}</Text>
      {failed ? (
        <AppPressable onPress={retry} style={styles.retry}>
          <Text style={styles.retryText}>{t("common.reconnect")}</Text>
        </AppPressable>
      ) : <ActivityIndicator color={colors.accent} />}
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
  const workspaceRoute = {
    pathname: "/workspaces/[workspaceId]" as const,
    params: { workspaceId: workspace.workspaceId, workspaceName: workspace.displayName },
  };
  if (preference.landing === "workspace" || !preference.nodeId) {
    router.replace(workspaceRoute);
    return;
  }
  const bindings = await listNodeBindings();
  if (!bindings.some((item) => item.nodeId === preference.nodeId)) {
    router.replace(workspaceRoute);
    return;
  }
  try {
    await gateway.switchNode(preference.nodeId);
    router.replace({
      pathname: "/node",
      params: {
        workspaceId: workspace.workspaceId,
        workspaceName: workspace.displayName,
        nodeId: preference.nodeId,
      },
    });
  } catch {
    router.replace(workspaceRoute);
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 28, backgroundColor: colors.background },
  mark: { width: 72, height: 72, borderRadius: 23, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  markText: { color: "white", fontSize: 30, fontWeight: "800" },
  title: { color: colors.ink, fontSize: 18, fontWeight: "700" },
  detail: { color: colors.muted, fontSize: 13, textAlign: "center", lineHeight: 20 },
  retry: { paddingHorizontal: 22, paddingVertical: 12, borderRadius: 13, backgroundColor: colors.accent },
  retryText: { color: "white", fontWeight: "800" },
});
