import { router, useLocalSearchParams } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportCompactLabelKey } from "@/api/transportPresentation";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { useI18n } from "@/i18n";

export function NodeHeaderTitle() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceName?: string }>();
  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  const statusLabel = gateway.status === "ready"
    ? `${t("nodeHeader.online")} · ${t(transportCompactLabelKey(gateway.transportMode))}`
    : t("nodeHeader.connecting");
  return (
    <View style={styles.titleWrap}>
      <Text style={styles.node} numberOfLines={1}>{node?.displayName || t("nav.node")}</Text>
      <Text style={styles.workspace} numberOfLines={1}>
        {stringParam(params.workspaceName) || t("nav.workspace")} · {statusLabel}
      </Text>
    </View>
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
  titleWrap: { minWidth: 0 },
  node: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  workspace: { color: colors.muted, fontSize: 11, marginTop: 1 },
  back: { width: 42, height: 42, alignItems: "center", justifyContent: "center", marginLeft: -8 },
});
