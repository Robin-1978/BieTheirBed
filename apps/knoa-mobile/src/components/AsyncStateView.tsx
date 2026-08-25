import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { colors, radii, shadows, spacing, typography } from "@/theme";

type AsyncState = "loading" | "error" | "empty";

export type AsyncStateViewProps = {
  state: AsyncState;
  title?: string;
  message?: string;
  retryLabel?: string;
  onRetry?: () => void;
};

/** One predictable presentation for async loading, failure, and empty data. */
export function AsyncStateView({
  state,
  title,
  message,
  retryLabel,
  onRetry,
}: AsyncStateViewProps) {
  if (state === "loading") {
    return <ActivityIndicator accessibilityLabel="Loading" color={colors.accent} style={styles.loading} />;
  }

  return (
    <View style={[styles.card, state === "error" ? styles.errorCard : styles.emptyCard]}>
      {title ? <Text style={styles.title}>{title}</Text> : null}
      {message ? <Text style={[styles.message, state === "error" && styles.errorMessage]}>{message}</Text> : null}
      {state === "error" && onRetry && retryLabel ? (
        <AppPressable accessibilityRole="button" onPress={onRetry} style={styles.retryButton}>
          <Text style={styles.retry}>{retryLabel}</Text>
        </AppPressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  loading: { marginTop: spacing.xlarge },
  card: { padding: spacing.large, borderRadius: radii.medium, gap: spacing.small, ...shadows.card },
  errorCard: { backgroundColor: colors.dangerSoft },
  emptyCard: { backgroundColor: colors.surface },
  title: { color: colors.ink, ...typography.subheading, textAlign: "center" },
  message: { color: colors.muted, textAlign: "center" },
  errorMessage: { color: colors.danger, textAlign: "left" },
  retryButton: { alignSelf: "flex-start" },
  retry: { color: colors.accent, fontWeight: "700" },
});
