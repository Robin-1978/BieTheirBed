import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportCompactLabelKey } from "@/api/transportPresentation";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, shadows, spacing } from "@/theme";
import { useI18n } from "@/i18n";
import { presentNodeName } from "@/presentation/nodePresentation";
import { loadCapabilityCache, type CapabilityCache } from "@/storage/capabilityCache";

export function NodeHeaderTitle() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const [switcherOpen, setSwitcherOpen] = useState(false);

  const workspaceId = stringParam(params.workspaceId);
  const workspaceName = stringParam(params.workspaceName) || t("nav.workspace");
  const currentNodeId = gateway.nodeId || stringParam(params.nodeId);
  const currentNode = gateway.nodes.find((item) => item.nodeId === currentNodeId);
  const isOnline = gateway.status === "ready";
  const statusLabel = isOnline
    ? `${t("nodeHeader.online")} · ${t(transportCompactLabelKey(gateway.transportMode))}`
    : t("nodeHeader.connecting");

  const [capability, setCapability] = useState<CapabilityCache | null>(null);

  useEffect(() => {
    if (!switcherOpen || !currentNodeId) return;
    let active = true;
    void loadCapabilityCache(currentNodeId).then((cached) => {
      if (active) setCapability(cached);
    });
    return () => { active = false; };
  }, [currentNodeId, switcherOpen]);

  const otherNodes = gateway.nodes.filter((item) => item.nodeId !== currentNodeId);

  const handleSwitchNode = async (targetNodeId: string) => {
    setSwitcherOpen(false);
    try {
      router.setParams({ nodeId: targetNodeId });
    } catch {
      // ignore route param update failures if layout is unmounted
    }
    await gateway.switchNode(targetNodeId);
  };

  const handleOpenNodeDetails = () => {
    setSwitcherOpen(false);
    const nodeParams = {
      workspaceId,
      workspaceName,
      nodeId: currentNodeId,
    };
    router.push({ pathname: "/settings/node", params: nodeParams });
  };

  const handleOpenCapabilities = () => {
    setSwitcherOpen(false);
    const nodeParams = {
      workspaceId,
      workspaceName,
      nodeId: currentNodeId,
    };
    router.push({ pathname: "/capabilities", params: nodeParams });
  };

  const handleReturnToWorkspace = () => {
    setSwitcherOpen(false);
    if (workspaceId) {
      router.push({ pathname: "/workspaces/[workspaceId]", params: { workspaceId, workspaceName } });
    } else {
      router.push("/account");
    }
  };

  const handleOpenAccount = () => {
    setSwitcherOpen(false);
    router.push("/account");
  };

  const handlePairNew = () => {
    setSwitcherOpen(false);
    router.push("/pair");
  };

  return (
    <>
      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={presentNodeName(currentNode, t("common.unnamedComputer"))}
        onPress={() => setSwitcherOpen(true)}
        style={styles.pillContainer}
      >
        <View style={[styles.statusDot, isOnline ? styles.dotOnline : styles.dotOffline]} />
        <View style={styles.titleWrap}>
          <View style={styles.nameRow}>
            <Text style={styles.node} numberOfLines={1}>
              {presentNodeName(currentNode, t("common.unnamedComputer"))}
            </Text>
            <AppIcon name="chevron-down" color={colors.muted} size={12} />
          </View>
          <Text style={styles.workspace} numberOfLines={1}>
            {statusLabel}
          </Text>
        </View>
      </AppPressable>

      <Modal
        animationType="fade"
        onRequestClose={() => setSwitcherOpen(false)}
        transparent
        visible={switcherOpen}
      >
        <View style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setSwitcherOpen(false)} />
          <View style={styles.drawerCard}>
            <View style={styles.handle} />

            <View style={styles.drawerHeader}>
              <Text style={styles.drawerTitle}>{t("nodeSwitch.title")}</Text>
              <AppPressable onPress={() => setSwitcherOpen(false)} style={styles.closeButton}>
                <AppIcon name="x" color={colors.muted} size={18} />
              </AppPressable>
            </View>

            <ScrollView style={styles.drawerScroll} contentContainerStyle={styles.drawerContent}>
              {/* 当前设备卡片 */}
              <View style={styles.currentNodeCard}>
                <View style={styles.currentNodeHeader}>
                  <View style={styles.nodeIconWrap}>
                    <AppIcon name="node" color={colors.accent} size={22} />
                  </View>
                  <View style={styles.flex}>
                    <Text style={styles.currentNodeName}>
                      {presentNodeName(currentNode, t("common.unnamedComputer"))}
                    </Text>
                    <View style={styles.statusRow}>
                      <View style={[styles.statusDot, isOnline ? styles.dotOnline : styles.dotOffline]} />
                      <Text style={styles.statusText}>{statusLabel}</Text>
                    </View>
                  </View>
                </View>

                {capability ? (
                  <View style={styles.capabilityStatsRow}>
                    <View style={styles.capabilityBadge}>
                      <AppIcon name="agent" color={colors.accent} size={12} />
                      <Text style={styles.capabilityBadgeText}>
                        {t("nodeSwitch.toolsCount", { count: capability.toolCount })}
                      </Text>
                    </View>
                    {capability.document.default_model ? (
                      <View style={styles.capabilityBadge}>
                        <AppIcon name="code" color={colors.muted} size={12} />
                        <Text style={styles.capabilityBadgeText} numberOfLines={1}>
                          {capability.document.default_model}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                ) : null}

                <AppPressable onPress={handleOpenCapabilities} style={styles.nodeDetailAction}>
                  <Text style={styles.nodeDetailActionText}>{t("nodeSwitch.capabilities")}</Text>
                  <AppIcon name="chevron-right" color={colors.accent} size={14} />
                </AppPressable>

                <AppPressable onPress={handleOpenNodeDetails} style={styles.nodeDetailAction}>
                  <Text style={styles.nodeDetailActionText}>{t("nodeSwitch.nodeDetails")}</Text>
                  <AppIcon name="chevron-right" color={colors.accent} size={14} />
                </AppPressable>
              </View>

              {/* 节点列表 / 切换节点 */}
              <View style={styles.section}>
                <View style={styles.sectionTitleRow}>
                  <Text style={styles.sectionTitle}>{t("nodeSwitch.otherNodes")}</Text>
                  <AppPressable onPress={handlePairNew} style={styles.pairNewButton}>
                    <AppIcon name="plus" color={colors.accent} size={14} />
                    <Text style={styles.pairNewText}>{t("nodeSwitch.pairNew")}</Text>
                  </AppPressable>
                </View>

                {otherNodes.length > 0 ? (
                  <View style={styles.nodesList}>
                    {otherNodes.map((item) => (
                      <AppPressable
                        key={item.nodeId}
                        onPress={() => void handleSwitchNode(item.nodeId)}
                        style={styles.nodeItem}
                      >
                        <AppIcon name="node" color={colors.muted} size={18} />
                        <View style={styles.flex}>
                          <Text style={styles.nodeItemName}>
                            {presentNodeName(item, t("common.unnamedComputer"))}
                          </Text>
                          <Text style={styles.nodeItemSub}>
                            {item.nodeId.slice(0, 8)}...
                          </Text>
                        </View>
                        <AppIcon name="refresh" color={colors.accent} size={16} />
                      </AppPressable>
                    ))}
                  </View>
                ) : (
                  <View style={styles.emptyOtherNodes}>
                    <Text style={styles.emptyOtherText}>{t("workspace.addNode")}</Text>
                  </View>
                )}
              </View>

              {/* 工作区大盘与账号流转 */}
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>{workspaceName}</Text>
                <AppPressable onPress={handleReturnToWorkspace} style={styles.navRow}>
                  <View style={styles.navIconWrap}>
                    <AppIcon name="workspace" color={colors.accent} size={18} />
                  </View>
                  <View style={styles.flex}>
                    <Text style={styles.navRowTitle}>{t("nodeSwitch.workspaceHub")}</Text>
                    <Text style={styles.navRowSub} numberOfLines={1}>{workspaceName}</Text>
                  </View>
                  <AppIcon name="chevron-right" color={colors.muted} size={16} />
                </AppPressable>

                <AppPressable onPress={handleOpenAccount} style={styles.navRow}>
                  <View style={styles.navIconWrap}>
                    <AppIcon name="settings" color={colors.accent} size={18} />
                  </View>
                  <View style={styles.flex}>
                    <Text style={styles.navRowTitle}>{t("nodeSwitch.accountHub")}</Text>
                    <Text style={styles.navRowSub}>{t("account.appSection")}</Text>
                  </View>
                  <AppIcon name="chevron-right" color={colors.muted} size={16} />
                </AppPressable>
              </View>
            </ScrollView>
          </View>
        </View>
      </Modal>
    </>
  );
}

export function NodeHeaderBack() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const workspaceId = stringParam(params.workspaceId);
  const workspaceName = stringParam(params.workspaceName);
  const nodeId = gateway.nodeId || stringParam(params.nodeId);
  return (
    <AppPressable
      accessibilityRole="button"
      accessibilityLabel={t("nodeHeader.back")}
      hitSlop={8}
      onPress={() => {
        if (router.canGoBack()) {
          router.back();
        } else {
          router.replace({
            pathname: "/(tabs)",
            params: { workspaceId, workspaceName, nodeId },
          });
        }
      }}
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

  modalRoot: {
    flex: 1,
    justifyContent: "flex-end",
  },
  backdrop: {
    ...StyleSheet.absoluteFill,
    backgroundColor: "rgba(0, 0, 0, 0.45)",
  },
  drawerCard: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    maxHeight: "82%",
    paddingBottom: spacing.xlarge,
    ...shadows.floating,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.line,
    alignSelf: "center",
    marginTop: spacing.small,
  },
  drawerHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.medium,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  drawerTitle: {
    color: colors.ink,
    fontSize: 16,
    fontWeight: "800",
  },
  closeButton: {
    padding: 6,
    borderRadius: radii.pill,
  },
  drawerScroll: {
    flexGrow: 0,
  },
  drawerContent: {
    padding: spacing.large,
    gap: spacing.large,
  },
  currentNodeCard: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.large,
    padding: spacing.medium,
    gap: spacing.medium,
    borderWidth: 1,
    borderColor: colors.line,
  },
  currentNodeHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
  },
  nodeIconWrap: {
    width: 40,
    height: 40,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  flex: {
    flex: 1,
    minWidth: 0,
  },
  currentNodeName: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "800",
  },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 2,
  },
  statusText: {
    color: colors.muted,
    fontSize: 12,
  },
  capabilityStatsRow: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: spacing.small,
  },
  capabilityBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.surface,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radii.small,
    borderWidth: 1,
    borderColor: colors.line,
  },
  capabilityBadgeText: {
    color: colors.ink,
    fontSize: 11,
    fontWeight: "600",
  },
  nodeDetailAction: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: spacing.small,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
  },
  nodeDetailActionText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  section: {
    gap: spacing.small,
  },
  sectionTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  sectionTitle: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
  },
  pairNewButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 2,
    paddingHorizontal: 6,
  },
  pairNewText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  nodesList: {
    gap: spacing.small,
  },
  nodeItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
  },
  nodeItemName: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "700",
  },
  nodeItemSub: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 2,
  },
  emptyOtherNodes: {
    padding: spacing.medium,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    borderStyle: "dashed",
    alignItems: "center",
    justifyContent: "center",
  },
  emptyOtherText: {
    color: colors.muted,
    fontSize: 12,
  },
  navRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    paddingVertical: spacing.small,
  },
  navIconWrap: {
    width: 36,
    height: 36,
    borderRadius: radii.medium,
    backgroundColor: colors.surfaceMuted,
    alignItems: "center",
    justifyContent: "center",
  },
  navRowTitle: {
    color: colors.ink,
    fontSize: 14,
    fontWeight: "700",
  },
  navRowSub: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 2,
  },
});
