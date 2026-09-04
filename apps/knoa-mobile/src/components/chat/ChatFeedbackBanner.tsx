import { StyleSheet, Text, View } from "react-native";

import type { Feedback } from "./types";
import { colors, radii, spacing, typography } from "@/theme";

export function ChatFeedbackBanner({ feedback }: { feedback: Feedback | null }) {
  if (!feedback) return null;

  return (
    <View
      style={[
        styles.feedback,
        feedback.tone === "error" && styles.feedbackError,
        feedback.tone === "warning" && styles.feedbackWarning,
        feedback.tone === "success" && styles.feedbackSuccess,
      ]}
    >
      <Text style={styles.feedbackText}>{feedback.text}</Text>
    </View>
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
  },
  feedbackError: {
    backgroundColor: "#fee2e2",
    borderColor: colors.danger,
  },
  feedbackWarning: {
    backgroundColor: "#fef3c7",
    borderColor: colors.warning,
  },
  feedbackSuccess: {
    backgroundColor: "#dcfce7",
    borderColor: colors.accent,
  },
  feedbackText: {
    color: colors.ink,
    ...typography.small,
    textAlign: "center",
  },
});
