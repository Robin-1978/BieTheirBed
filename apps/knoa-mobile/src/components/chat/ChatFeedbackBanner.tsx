import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import type { Feedback } from "./types";
import { colors, radii, spacing, typography } from "@/theme";

export function ChatFeedbackBanner({
  feedback,
  onDismiss,
}: {
  feedback: Feedback | null;
  onDismiss?: () => void;
}) {
  if (!feedback) return null;

  const tone = feedback.tone || "info";

  return (
    <AppPressable
      accessibilityRole="alert"
      onPress={onDismiss}
      style={[
        styles.feedback,
        tone === "error" && styles.feedbackError,
        tone === "warning" && styles.feedbackWarning,
        tone === "success" && styles.feedbackSuccess,
      ]}
    >
      <View style={styles.iconWrap}>
        <AppIcon
          name={tone === "error" ? "alert" : tone === "warning" ? "alert" : tone === "success" ? "check" : "pulse"}
          color={tone === "error" ? colors.danger : tone === "warning" ? colors.warning : tone === "success" ? colors.accent : colors.ink}
          size={16}
        />
      </View>

      <Text style={styles.feedbackText} numberOfLines={2}>
        {feedback.text}
      </Text>

      {onDismiss ? (
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel="Close notification"
          onPress={onDismiss}
          style={styles.closeButton}
          hitSlop={8}
        >
          <AppIcon name="x" color={colors.muted} size={15} />
        </AppPressable>
      ) : null}
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  feedback: {
    marginHorizontal: spacing.large,
    marginVertical: spacing.small,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  iconWrap: {
    alignItems: "center",
    justifyContent: "center",
  },
  feedbackError: {
    backgroundColor: colors.dangerSoft,
    borderColor: colors.danger,
  },
  feedbackWarning: {
    backgroundColor: colors.warningSoft,
    borderColor: colors.warning,
  },
  feedbackSuccess: {
    backgroundColor: colors.accentSoft,
    borderColor: colors.accent,
  },
  feedbackText: {
    flex: 1,
    color: colors.ink,
    ...typography.small,
    lineHeight: 18,
  },
  closeButton: {
    padding: 4,
    alignItems: "center",
    justifyContent: "center",
  },
});
