import { router, useLocalSearchParams } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportCompactLabelKey } from "@/api/transportPresentation";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii } from "@/theme";
import { useI18n } from "@/i18n";
import { presentNodeName } from "@/presentation/nodePresentation";

export function NodeHeaderTitle() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  const isOnline = gateway.status === "ready";
  const statusLabel = isOnline
    ? `${t("nodeHeader.online")} · ${t(transportCompactLabelKey(gateway.transportMode))}`
    : t("nodeHeader.connecting");

  const handlePress = () => {
    const nodeParams = {
      workspaceId: stringParam(params.workspaceId),
      workspaceName: stringParam(params.workspaceName),
      nodeId: stringParam(params.nodeId) || gateway.nodeId,
    };
    router.push({ pathname: "/node", params: nodeParams });
  };

  return (
    <AppPressable
      accessibilityRole="button"
      accessibilityLabel={presentNodeName(node, t("common.unnamedComputer"))}
      onPress={handlePress}
      style={styles.pillContainer}
    >
      <View style={[styles.statusDot, isOnline ? styles.dotOnline : styles.dotOffline]} />
      <View style={styles.titleWrap}>
        <View style={styles.nameRow}>
          <Text style={styles.node} numberOfLines={1}>
            {presentNodeName(node, t("common.unnamedComputer"))}
          </Text>
          <AppIcon name="chevron-down" color={colors.muted} size={12} />
        </View>
        <Text style={styles.workspace} numberOfLines={1}>
          {statusLabel}
        </Text>
      </View>
    </AppPressable>
  );
}

export function NodeHeaderBack() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  const workspaceName = stringParam(params.workspaceName);
  const nodeId = stringParam(params.nodeId) || gateway.nodeId;
  return (
    <AppPressable
      accessibilityRole="button"
      accessibilityLabel={t("nodeHeader.back")}
      hitSlop={8}
      onPress={() => nodeId
        ? router.replace({
            pathname: "/node",
            params: { workspaceId, workspaceName, nodeId },
          })
        : workspaceId
          ? router.replace({
              pathname: "/workspaces/[workspaceId]",
              params: { workspaceId, workspaceName },
            })
          : router.replace("/account")}
      style={styles.back}
    >
      <AppIcon name="chevron-left" color={colors.ink} size={25} />
    </AppPressable>
  );
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  pillContainer: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: radii.medium,
    backgroundColor: colors.surfaceMuted,
    gap: 8,
    maxWidth: 240,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  dotOnline: {
    backgroundColor: colors.accent,
  },
  dotOffline: {
    backgroundColor: colors.warning,
  },
  nameRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  titleWrap: {
    minWidth: 0,
    flexShrink: 1,
  },
  node: { color: colors.ink, fontSize: 13, fontWeight: "800" },
  workspace: { color: colors.muted, fontSize: 10, marginTop: 1 },
  back: { width: 42, height: 42, alignItems: "center", justifyContent: "center", marginLeft: -8 },
});
