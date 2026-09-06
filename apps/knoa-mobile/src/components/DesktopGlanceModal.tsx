import React, { useState } from "react";
import {
  ActivityIndicator,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  StyleSheet,
  Text,
  TextInput,
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
  onRefresh?: () => void;
  refreshing?: boolean;
  onSteer?: (instruction: string) => Promise<void> | void;
}

export function DesktopGlanceModal({
  glance,
  visible,
  onClose,
  onRefresh,
  refreshing,
  onSteer,
}: DesktopGlanceModalProps) {
  const { t } = useI18n();

  const [steerInput, setSteerInput] = useState("");
  const [steerSending, setSteerSending] = useState(false);
  const [steerFeedback, setSteerFeedback] = useState("");

  if (!visible) return null;

  const sampleTime = glance?.timestamp
    ? new Date(glance.timestamp).toLocaleTimeString()
    : "";

  const handleSendSteer = async () => {
    const text = steerInput.trim();
    if (!text || !onSteer || steerSending) return;
    setSteerSending(true);
    setSteerFeedback("");
    try {
      await onSteer(text);
      setSteerInput("");
      setSteerFeedback(t("tasks.steerSuccess"));
    } catch {
      setSteerFeedback("插话失败，请重试");
    } finally {
      setSteerSending(false);
    }
  };

  return (
    <Modal
      transparent
      animationType="fade"
      visible={visible}
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.backdrop}
      >
        <AppPressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.modalCard}>
          {/* 模态框顶部导航 */}
          <View style={styles.header}>
            <View style={styles.headerTitleWrap}>
              <AppIcon name="desktop" color={colors.accent} size={16} />
              <Text style={styles.headerTitle}>{t("tasks.bentoGlance")}</Text>
            </View>
            <View style={styles.headerRightActions}>
              {onRefresh ? (
                <AppPressable
                  style={[styles.refreshButton, refreshing && styles.refreshing]}
                  onPress={onRefresh}
                  disabled={refreshing}
                >
                  <AppIcon name="refresh" color={colors.accent} size={15} />
                </AppPressable>
              ) : null}
              <AppPressable style={styles.closeButton} onPress={onClose}>
                <AppIcon name="x" color={colors.muted} size={16} />
              </AppPressable>
            </View>
          </View>

          {/* 桌面图像大图 */}
          <View style={styles.imageContainer}>
            {refreshing && !glance?.thumbnailBase64 ? (
              <View style={styles.loadingWrap}>
                <ActivityIndicator color={colors.accent} size="large" />
                <Text style={styles.loadingText}>正在采样真机桌面画面...</Text>
              </View>
            ) : glance?.thumbnailBase64 ? (
              <Image
                source={{ uri: `data:image/jpeg;base64,${glance.thumbnailBase64}` }}
                style={styles.image}
                resizeMode="contain"
              />
            ) : (
              <View style={styles.emptyImageWrap}>
                <AppIcon name="desktop" color={colors.muted} size={32} />
                <Text style={styles.emptyText}>暂无桌面画面，点击右上角刷新</Text>
              </View>
            )}
          </View>

          {/* 元数据详情行 */}
          <View style={styles.metaContainer}>
            {glance?.activeApp ? (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>App:</Text>
                <Text style={styles.metaValue} numberOfLines={1}>
                  {glance.activeApp}
                </Text>
              </View>
            ) : null}

            {glance?.windowTitle ? (
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

          {/* 实时插话纠偏 (Live Steering) 输入栏 */}
          {onSteer ? (
            <View style={styles.steerSection}>
              <View style={styles.steerHeader}>
                <AppIcon name="agent" color={colors.accent} size={13} />
                <Text style={styles.steerTitle}>{t("tasks.steerTitle")}</Text>
              </View>
              <View style={styles.steerInputRow}>
                <TextInput
                  value={steerInput}
                  onChangeText={(val) => {
                    setSteerInput(val);
                    if (steerFeedback) setSteerFeedback("");
                  }}
                  placeholder={t("tasks.steerPlaceholder")}
                  placeholderTextColor={colors.muted}
                  style={styles.steerInput}
                />
                <AppPressable
                  disabled={!steerInput.trim() || steerSending}
                  style={[
                    styles.steerSendBtn,
                    (!steerInput.trim() || steerSending) && styles.steerSendBtnDisabled,
                  ]}
                  onPress={handleSendSteer}
                >
                  {steerSending ? (
                    <ActivityIndicator color={colors.onAccent} size="small" />
                  ) : (
                    <AppIcon name="send" color={colors.onAccent} size={13} />
                  )}
                </AppPressable>
              </View>
              {steerFeedback ? (
                <Text style={styles.steerFeedbackText}>{steerFeedback}</Text>
              ) : null}
            </View>
          ) : null}
        </View>
      </KeyboardAvoidingView>
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
  headerRightActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  refreshButton: {
    padding: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.surfaceMuted,
  },
  refreshing: {
    opacity: 0.5,
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
    gap: spacing.small,
  },
  emptyText: {
    color: colors.muted,
    fontSize: 12,
  },
  loadingWrap: {
    justifyContent: "center",
    alignItems: "center",
    gap: spacing.small,
  },
  loadingText: {
    color: "#94A3B8",
    fontSize: 12,
    fontWeight: "500",
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
  steerSection: {
    paddingHorizontal: spacing.medium,
    paddingBottom: spacing.medium,
    gap: spacing.small,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
    backgroundColor: colors.surface,
  },
  steerHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingTop: 8,
  },
  steerTitle: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "700",
  },
  steerInputRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  steerInput: {
    flex: 1,
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.medium,
    paddingHorizontal: spacing.medium,
    paddingVertical: 8,
    fontSize: 13,
    color: colors.ink,
    borderWidth: 1,
    borderColor: colors.line,
  },
  steerSendBtn: {
    backgroundColor: colors.accent,
    borderRadius: radii.medium,
    paddingHorizontal: 12,
    paddingVertical: 9,
    justifyContent: "center",
    alignItems: "center",
  },
  steerSendBtnDisabled: {
    opacity: 0.5,
  },
  steerFeedbackText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "600",
  },
});
