import React from "react";
import {
  Image,
  Modal,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { DesktopGlanceRecord } from "@/api/models";
import { useI18n } from "@/i18n";
import { colors, radii, shadows, spacing } from "@/theme";
import { AppIcon } from "./AppIcon";
import { AppPressable } from "./AppPressable";

export interface DesktopGlanceModalProps {
  glance: DesktopGlanceRecord | null;
  visible: boolean;
  onClose: () => void;
}

export function DesktopGlanceModal({
  glance,
  visible,
  onClose,
}: DesktopGlanceModalProps) {
  const { t } = useI18n();

  if (!glance || !visible) return null;

  const sampleTime = glance.timestamp
    ? new Date(glance.timestamp).toLocaleTimeString()
    : "";

  return (
    <Modal
      transparent
      animationType="fade"
      visible={visible}
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        <AppPressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.modalCard}>
          {/* 模态框顶部导航 */}
          <View style={styles.header}>
            <View style={styles.headerTitleWrap}>
              <AppIcon name="desktop" color={colors.accent} size={16} />
              <Text style={styles.headerTitle}>{t("tasks.bentoGlance")}</Text>
            </View>
            <AppPressable style={styles.closeButton} onPress={onClose}>
              <AppIcon name="x" color={colors.muted} size={16} />
            </AppPressable>
          </View>

          {/* 桌面图像大图 */}
          <View style={styles.imageContainer}>
            {glance.thumbnailBase64 ? (
              <Image
                source={{ uri: `data:image/jpeg;base64,${glance.thumbnailBase64}` }}
                style={styles.image}
                resizeMode="contain"
              />
            ) : (
              <View style={styles.emptyImageWrap}>
                <AppIcon name="desktop" color={colors.muted} size={32} />
              </View>
            )}
          </View>

          {/* 元数据详情行 */}
          <View style={styles.metaContainer}>
            {glance.activeApp ? (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>App:</Text>
                <Text style={styles.metaValue} numberOfLines={1}>
                  {glance.activeApp}
                </Text>
              </View>
            ) : null}

            {glance.windowTitle ? (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Window:</Text>
                <Text style={styles.metaValue} numberOfLines={2}>
                  {glance.windowTitle}
                </Text>
              </View>
            ) : null}

            {sampleTime ? (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Time:</Text>
                <Text style={styles.metaValue}>{sampleTime}</Text>
              </View>
            ) : null}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0, 0, 0, 0.65)",
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.large,
  },
  modalCard: {
    width: "100%",
    maxWidth: 420,
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: "hidden",
    ...shadows.card,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.medium,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  headerTitleWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  headerTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "700",
  },
  closeButton: {
    padding: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.surfaceMuted,
  },
  imageContainer: {
    width: "100%",
    height: 220,
    backgroundColor: "#0F172A",
    justifyContent: "center",
    alignItems: "center",
  },
  image: {
    width: "100%",
    height: "100%",
  },
  emptyImageWrap: {
    justifyContent: "center",
    alignItems: "center",
  },
  metaContainer: {
    padding: spacing.medium,
    gap: spacing.small,
    backgroundColor: colors.surface,
  },
  metaRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: spacing.small,
  },
  metaLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
    width: 60,
  },
  metaValue: {
    color: colors.ink,
    fontSize: 12,
    flex: 1,
    fontWeight: "500",
  },
});
