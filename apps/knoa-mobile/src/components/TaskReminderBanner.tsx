import { router } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Animated, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { colors, radii, shadows, spacing } from "@/theme";

export function TaskReminderBanner() {
  const { activeReminder, unreadCount, dismissActive, markRead } = useTaskReminders();
  const gateway = useGateway();
  const { t } = useI18n();
  const insets = useSafeAreaInsets();
  const translateY = useRef(new Animated.Value(-24)).current;
  const opacity = useRef(new Animated.Value(0)).current;
  const isApproval = activeReminder?.category === "approval";
  const [approving, setApproving] = useState(false);

  useEffect(() => {
    if (!activeReminder) return;
    translateY.setValue(-24);
    opacity.setValue(0);
    Animated.parallel([
      Animated.spring(translateY, { toValue: 0, useNativeDriver: true, damping: 18, stiffness: 220 }),
      Animated.timing(opacity, { toValue: 1, duration: 160, useNativeDriver: true }),
    ]).start();
    const timer = setTimeout(dismissActive, isApproval ? 30000 : 6500);
    return () => clearTimeout(timer);
  }, [activeReminder, dismissActive, isApproval, opacity, translateY]);

  if (!activeReminder) return null;
  const title = unreadCount > 1
    ? t("reminders.summary", { count: unreadCount })
    : activeReminder.category === "completed"
      ? t("reminders.completed")
      : activeReminder.category === "failed"
        ? t("reminders.failed")
        : t("reminders.approval");

  function open() {
    markRead(activeReminder!.reminderId);
    dismissActive();
    if (activeReminder!.executionId) {
      router.push(`/task-executions/${activeReminder!.executionId}`);
    } else if (activeReminder!.taskId) {
      router.push(`/tasks/${activeReminder!.taskId}`);
    }
  }

  async function quickResolve(approved: boolean) {
    if (!activeReminder?.executionId || approving || !gateway.client) return;
    setApproving(true);
    try {
      const snapshot = await gateway.runAuthenticated((client) => client.getTaskExecution(activeReminder.executionId));
      const pendingApproval = snapshot.approvals.find((a) => a.state === "pending");
      if (pendingApproval) {
        await gateway.runAuthenticated((client) => client.resolveApproval(pendingApproval.approval_id, approved));
      }
      markRead(activeReminder.reminderId);
      dismissActive();
    } catch {
      // If quick resolve fails, fall back to opening the execution page
      open();
    } finally {
      setApproving(false);
    }
  }

  return (
    <Animated.View
      pointerEvents="box-none"
      style={[styles.position, { top: insets.top + 54, opacity, transform: [{ translateY }] }]}
    >
      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={title}
        onPress={open}
        style={[styles.banner, isApproval && styles.bannerApproval]}
      >
        <View style={[styles.icon, isApproval && styles.iconApproval]}>
          <AppIcon
            name={activeReminder.category === "failed" ? "x" : isApproval ? "alert" : "check"}
            color={activeReminder.category === "failed" ? colors.danger : isApproval ? colors.warning : colors.accent}
            size={18}
          />
        </View>
        <View style={styles.copy}>
          <Text style={[styles.title, isApproval && styles.titleApproval]}>{title}</Text>
          <Text numberOfLines={1} style={styles.detail}>
            {activeReminder.nodeName ? `${t("reminders.deviceFrom", { device: activeReminder.nodeName })} · ` : ""}
            {activeReminder.taskTitle}
          </Text>
        </View>
        {isApproval ? (
          <View style={styles.approvalActions}>
            <AppPressable
              accessibilityRole="button"
              accessibilityLabel={t("reminders.quickApprove")}
              disabled={approving}
              onPress={(event) => {
                event.stopPropagation();
                void quickResolve(true);
              }}
              style={[styles.quickButton, styles.quickApprove]}
            >
              {approving ? (
                <ActivityIndicator color={colors.onAccent} size="small" />
              ) : (
                <Text style={styles.quickApproveText}>{t("reminders.quickApprove")}</Text>
              )}
            </AppPressable>
            <AppPressable
              accessibilityRole="button"
              accessibilityLabel={t("reminders.quickReject")}
              disabled={approving}
              onPress={(event) => {
                event.stopPropagation();
                void quickResolve(false);
              }}
              style={[styles.quickButton, styles.quickReject]}
            >
              <Text style={styles.quickRejectText}>{t("reminders.quickReject")}</Text>
            </AppPressable>
          </View>
        ) : null}
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={t("reminders.dismiss")}
          hitSlop={10}
          onPress={(event) => {
            event.stopPropagation();
            markRead(activeReminder.reminderId);
            dismissActive();
          }}
          style={styles.close}
        >
          <AppIcon name="x" color={colors.muted} size={17} />
        </AppPressable>
      </AppPressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  position: { position: "absolute", left: 14, right: 14, zIndex: 100 },
  banner: {
    minHeight: 66,
    paddingHorizontal: 13,
    paddingVertical: 11,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surfaceElevated,
    flexDirection: "row",
    alignItems: "center",
    gap: 11,
    ...shadows.floating,
  },
  bannerApproval: {
    backgroundColor: colors.warningSoft,
    borderColor: colors.warning,
  },
  icon: { width: 34, height: 34, borderRadius: 17, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" },
  iconApproval: { backgroundColor: colors.surfaceElevated },
  copy: { flex: 1, gap: 2 },
  title: { color: colors.ink, fontSize: 15, fontWeight: "700" },
  titleApproval: { color: colors.warning },
  detail: { color: colors.muted, fontSize: 13 },
  approvalActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  quickButton: {
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: radii.small,
    alignItems: "center",
    justifyContent: "center",
    minWidth: 44,
  },
  quickApprove: {
    backgroundColor: colors.accent,
  },
  quickApproveText: {
    color: colors.onAccent,
    fontSize: 12,
    fontWeight: "700",
  },
  quickReject: {
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.line,
  },
  quickRejectText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
  },
  actionPill: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceElevated,
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.warning,
    gap: 2,
  },
  actionPillText: {
    color: colors.warning,
    fontSize: 12,
    fontWeight: "700",
  },
  close: { width: 30, height: 30, alignItems: "center", justifyContent: "center", borderRadius: 10 },
});
