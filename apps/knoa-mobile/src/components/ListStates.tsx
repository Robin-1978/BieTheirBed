import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { colors, radii, spacing, typography } from "@/theme";

type Props = {
  rows?: number;
};

/** Skeleton placeholder rows for lists. Feels instant compared to a spinner. */
export function SkeletonList({ rows = 3 }: Props) {
  return (
    <View style={styles.list} accessibilityLabel="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <View key={index} style={styles.card}>
          <View style={styles.titleBar} />
          <View style={styles.line} />
          <View style={[styles.line, styles.short]} />
        </View>
      ))}
    </View>
  );
}

type EmptyProps = {
  icon?: "tasks" | "bell" | "folder" | "chat";
  title: string;
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
};

/** Empty state with an exit: never a dead end. */
export function EmptyState({ icon = "tasks", title, message, actionLabel, onAction }: EmptyProps) {
  return (
    <View style={styles.empty}>
      <AppIcon name={icon} color={colors.muted} size={40} />
      <Text style={styles.emptyTitle}>{title}</Text>
      {message ? <Text style={styles.emptyMessage}>{message}</Text> : null}
      {actionLabel && onAction ? (
        <AppPressable accessibilityRole="button" onPress={onAction} style={styles.action}>
          <Text style={styles.actionText}>{actionLabel}</Text>
        </AppPressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  list: { gap: spacing.small },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.medium,
    gap: spacing.small,
  },
  titleBar: { height: 18, borderRadius: 6, backgroundColor: colors.line, width: "55%" },
  line: { height: 12, borderRadius: 6, backgroundColor: colors.line, width: "100%" },
  short: { width: "70%" },
  empty: { alignItems: "center", gap: spacing.small, padding: spacing.xlarge },
  emptyTitle: { ...typography.subheading, color: colors.ink, textAlign: "center" },
  emptyMessage: { ...typography.caption, color: colors.muted, textAlign: "center" },
  action: {
    backgroundColor: colors.accent,
    borderRadius: radii.medium,
    paddingHorizontal: spacing.xlarge,
    paddingVertical: spacing.small,
    marginTop: spacing.xsmall,
  },
  actionText: { color: colors.onAccent, fontWeight: "700" },
});
