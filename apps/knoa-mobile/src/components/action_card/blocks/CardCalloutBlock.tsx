import React from "react";
import { StyleSheet, Text, View } from "react-native";

import type { AppIconName } from "@/components/AppIcon";
import { AppIcon } from "@/components/AppIcon";
import { colors, radii, spacing, typography } from "@/theme";
import type { CardCalloutBlock as CardCalloutBlockType } from "../types";

const CALLOUT_THEMES: Record<string, { bg: typeof colors.infoSoft; border: typeof colors.info; text: typeof colors.ink; icon: AppIconName }> = {
  info: {
    bg: colors.infoSoft,
    border: colors.info,
    text: colors.ink,
    icon: "alert",
  },
  warning: {
    bg: colors.warningSoft,
    border: colors.warning,
    text: colors.ink,
    icon: "alert",
  },
  error: {
    bg: colors.dangerSoft,
    border: colors.danger,
    text: colors.ink,
    icon: "alert",
  },
};

export function CardCalloutBlock({ block }: { block: CardCalloutBlockType }) {
  const theme = CALLOUT_THEMES[block.level] ?? CALLOUT_THEMES.info;
  if (!theme) return null;

  return (
    <View style={[styles.container, { backgroundColor: theme.bg, borderLeftColor: theme.border }]}>
      <View style={styles.icon}>
        <AppIcon name={theme.icon} size={16} color={theme.border} />
      </View>
      <Text style={[styles.text, { color: theme.text }]}>{block.text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "flex-start",
    padding: spacing.medium,
    borderRadius: radii.medium,
    borderLeftWidth: 3,
    marginVertical: spacing.xsmall,
    gap: spacing.small,
  },
  icon: {
    marginTop: 2,
    flexShrink: 0,
  },
  text: {
    fontSize: typography.body.fontSize,
    lineHeight: 20,
    flex: 1,
  },
});
