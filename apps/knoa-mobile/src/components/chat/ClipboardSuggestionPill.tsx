import { memo } from "react";
import { StyleSheet, Text, View } from "react-native";

import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors, radii, shadows, spacing } from "@/theme";

export type ClipboardSuggestion = {
  text: string;
  kind: "url" | "code" | "text";
};

type ClipboardSuggestionPillProps = {
  suggestion: ClipboardSuggestion;
  onApply(text: string): void;
  onDismiss(): void;
};

export const ClipboardSuggestionPill = memo(function ClipboardSuggestionPill({
  suggestion,
  onApply,
  onDismiss,
}: ClipboardSuggestionPillProps) {
  const { t } = useI18n();

  const iconName: AppIconName = suggestion.kind === "url"
    ? "globe"
    : suggestion.kind === "code"
      ? "code"
      : "file";

  const actionTitle = suggestion.kind === "url"
    ? t("chat.clipboardUrl")
    : suggestion.kind === "code"
      ? t("chat.clipboardCode")
      : t("chat.clipboardText");

  const previewSnippet = suggestion.text.replace(/\s+/g, " ").trim();
  const truncatedPreview = previewSnippet.length > 36 ? `${previewSnippet.slice(0, 36)}…` : previewSnippet;

  const handlePress = () => {
    if (suggestion.kind === "url") {
      onApply(`${t("chat.clipboardUrl")}: ${suggestion.text}`);
    } else {
      onApply(suggestion.text);
    }
  };

  return (
    <View style={styles.container}>
      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={`${actionTitle} - ${truncatedPreview}`}
        onPress={handlePress}
        style={styles.pill}
      >
        <View style={styles.iconWrap}>
          <AppIcon name={iconName} color={colors.accent} size={15} />
        </View>
        <View style={styles.textWrap}>
          <Text style={styles.actionTitle} numberOfLines={1}>{actionTitle}</Text>
          <Text style={styles.previewText} numberOfLines={1}>{truncatedPreview}</Text>
        </View>
      </AppPressable>

      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={t("common.close")}
        hitSlop={8}
        onPress={onDismiss}
        style={styles.closeButton}
      >
        <AppIcon name="x" color={colors.muted} size={15} />
      </AppPressable>
    </View>
  );
});

const styles = StyleSheet.create({
  container: {
    marginHorizontal: spacing.large,
    marginBottom: spacing.small,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    paddingLeft: spacing.small,
    paddingRight: 6,
    paddingVertical: 6,
    gap: spacing.small,
    ...shadows.card,
  },
  pill: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    minWidth: 0,
  },
  iconWrap: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  textWrap: {
    flex: 1,
    minWidth: 0,
  },
  actionTitle: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  previewText: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 1,
  },
  closeButton: {
    padding: 6,
    borderRadius: radii.pill,
  },
});
