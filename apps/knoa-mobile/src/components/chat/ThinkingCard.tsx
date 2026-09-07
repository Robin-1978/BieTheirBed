import { memo, useState, useEffect } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { AppIcon } from "@/components/AppIcon";
import { useI18n } from "@/i18n";
import { colors, radii, spacing } from "@/theme";
import { formatThinkingDisplay } from "./thinkingPresentation";

export type ThinkingCardProps = {
  reasoning: string;
  isThinking: boolean;
};

export const ThinkingCard = memo(function ThinkingCard({
  reasoning,
  isThinking,
}: ThinkingCardProps) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(isThinking);

  useEffect(() => {
    if (isThinking) {
      setExpanded(true);
    }
  }, [isThinking]);

  const display = formatThinkingDisplay(reasoning, isThinking);
  if (!display) return null;

  return (
    <View style={styles.container}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={expanded ? t("chat.thoughtCollapse") : t("chat.thoughtExpand")}
        onPress={() => setExpanded((prev) => !prev)}
        style={styles.header}
      >
        <View style={styles.titleRow}>
          {display.isThinking ? (
            <ActivityIndicator color={colors.accent} size="small" style={styles.indicator} />
          ) : (
            <AppIcon name="agent" size={14} color={colors.muted} />
          )}
          <Text style={[styles.title, display.isThinking && styles.titleThinking]}>
            {t(display.titleKey)}
          </Text>
        </View>
        <Text style={styles.toggleText}>
          {expanded ? t("turn.collapseShort") : t("turn.view")}
        </Text>
      </Pressable>

      {expanded ? (
        <View style={styles.body}>
          <Text style={styles.reasoningText}>
            {display.cleanedText}
          </Text>
        </View>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  container: {
    marginTop: spacing.small,
    marginBottom: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.surfaceMuted,
    borderLeftWidth: 3,
    borderLeftColor: colors.accent,
    overflow: "hidden",
  },
  header: {
    minHeight: 36,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  indicator: {
    marginRight: 2,
  },
  title: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.muted,
  },
  titleThinking: {
    color: colors.accent,
  },
  toggleText: {
    fontSize: 12,
    fontWeight: "600",
    color: colors.accent,
  },
  body: {
    paddingHorizontal: spacing.medium,
    paddingBottom: spacing.medium,
    paddingTop: spacing.xsmall,
  },
  reasoningText: {
    fontSize: 13,
    lineHeight: 19,
    color: colors.muted,
    fontStyle: "italic",
  },
});
