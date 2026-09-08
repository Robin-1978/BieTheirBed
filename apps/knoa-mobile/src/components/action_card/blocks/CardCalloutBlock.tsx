import React from "react";
import { StyleSheet, Text, View } from "react-native";

import type { AppIconName } from "@/components/AppIcon";
import { AppIcon } from "@/components/AppIcon";
import { radii, spacing, typography } from "@/theme";
import type { CardCalloutBlock as CardCalloutBlockType } from "../types";

const CALLOUT_THEMES: Record<string, { bg: string; border: string; text: string; icon: AppIconName }> = {
  info: {
    bg: "#F0F9FF",
    border: "#0284C7",
    text: "#0C4A6E",
    icon: "alert",
  },
  warning: {
    bg: "#FFFBEB",
    border: "#D97706",
    text: "#78350F",
    icon: "alert",
  },
  error: {
    bg: "#FEF2F2",
    border: "#DC2626",
    text: "#7F1D1D",
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
