import { router, useLocalSearchParams } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportCompactLabel } from "@/api/transportPresentation";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export function NodeHeaderTitle() {
  const gateway = useGateway();
  const params = useLocalSearchParams<{ workspaceName?: string }>();
  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  return (
    <View style={styles.titleWrap}>
      <Text style={styles.node} numberOfLines={1}>{node?.displayName || "Node"}</Text>
      <Text style={styles.workspace} numberOfLines={1}>
        {stringParam(params.workspaceName) || "Workspace"} · {gateway.status === "ready"
          ? `在线 · ${transportCompactLabel(gateway.transportMode)}`
          : "连接中"}
      </Text>
    </View>
  );
}

export function NodeHeaderBack() {
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  return (
    <AppPressable
      accessibilityRole="button"
      accessibilityLabel="返回 Workspace"
      hitSlop={8}
      onPress={() => workspaceId
        ? router.replace({
            pathname: "/workspaces/[workspaceId]",
            params: { workspaceId, workspaceName: stringParam(params.workspaceName) },
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
