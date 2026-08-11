import { router } from "expo-router";
import { useEffect, useRef } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { colors, shadows } from "@/theme";

export function TaskReminderBanner() {
  const { activeReminder, unreadCount, dismissActive, markRead } = useTaskReminders();
  const { t } = useI18n();
  const insets = useSafeAreaInsets();
  const translateY = useRef(new Animated.Value(-24)).current;
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!activeReminder) return;
    translateY.setValue(-24);
    opacity.setValue(0);
    Animated.parallel([
      Animated.spring(translateY, { toValue: 0, useNativeDriver: true, damping: 18, stiffness: 220 }),
      Animated.timing(opacity, { toValue: 1, duration: 160, useNativeDriver: true }),
    ]).start();
    const timer = setTimeout(dismissActive, 6500);
    return () => clearTimeout(timer);
  }, [activeReminder, dismissActive, opacity, translateY]);

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
    router.push(`/task-executions/${activeReminder!.executionId}`);
  }

  return (
    <Animated.View
      pointerEvents="box-none"
      style={[styles.position, { top: insets.top + 54, opacity, transform: [{ translateY }] }]}
    >
      <AppPressable accessibilityRole="button" accessibilityLabel={title} onPress={open} style={styles.banner}>
        <View style={styles.icon}>
          <AppIcon name={activeReminder.category === "failed" ? "x" : "check"} color={colors.accent} size={18} />
        </View>
        <View style={styles.copy}>
          <Text style={styles.title}>{title}</Text>
          <Text numberOfLines={1} style={styles.detail}>{activeReminder.taskTitle}</Text>
        </View>
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={t("reminders.dismiss")}
          hitSlop={10}
          onPress={(event) => {
            event.stopPropagation();
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
  icon: { width: 34, height: 34, borderRadius: 17, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" },
  copy: { flex: 1, gap: 2 },
  title: { color: colors.ink, fontSize: 15, fontWeight: "700" },
  detail: { color: colors.muted, fontSize: 13 },
  close: { width: 30, height: 30, alignItems: "center", justifyContent: "center", borderRadius: 10 },
});
