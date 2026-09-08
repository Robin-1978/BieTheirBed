import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { colors, radii, spacing, typography } from "@/theme";
import type { CardKeyValueBlock as CardKeyValueBlockType, CardKeyValueItem } from "../types";

export function CardKeyValueBlock({ block }: { block: CardKeyValueBlockType }) {
  if (!block.items || block.items.length === 0) return null;

  return (
    <View style={styles.container}>
      {block.items.map((item, index) => (
        <KeyValueRow key={`${item.key}-${index}`} item={item} isLast={index === block.items.length - 1} />
      ))}
    </View>
  );
}

function KeyValueRow({ item, isLast }: { item: CardKeyValueItem; isLast: boolean }) {
  const valueContent = () => {
    switch (item.style) {
      case "code":
        return (
          <View style={styles.codeBadge}>
            <Text style={styles.codeText}>{item.value}</Text>
          </View>
        );
      case "badge":
        return (
          <View style={styles.capsuleBadge}>
            <Text style={styles.badgeText}>{item.value}</Text>
          </View>
        );
      case "bold":
        return <Text style={[styles.valueText, styles.boldText]}>{item.value}</Text>;
      case "muted":
        return <Text style={[styles.valueText, styles.mutedText]}>{item.value}</Text>;
      default:
        return <Text style={styles.valueText}>{item.value}</Text>;
    }
  };

  return (
    <View style={[styles.row, !isLast && styles.rowBorder]}>
      <Text style={styles.keyText}>{item.key}</Text>
      <View style={styles.valueContainer}>{valueContent()}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.medium,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    marginVertical: spacing.xsmall,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: spacing.small,
  },
  rowBorder: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  keyText: {
    fontSize: typography.caption.fontSize,
    color: colors.muted,
    flexShrink: 0,
    marginRight: spacing.small,
  },
  valueContainer: {
    flexShrink: 1,
    alignItems: "flex-end",
  },
  valueText: {
    fontSize: typography.caption.fontSize,
    color: colors.ink,
    textAlign: "right",
  },
  boldText: {
    fontWeight: "700",
  },
  mutedText: {
    color: colors.muted,
  },
  codeBadge: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.small,
    paddingHorizontal: spacing.small,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: colors.line,
  },
  codeText: {
    fontSize: typography.tiny.fontSize,
    fontFamily: "monospace",
    color: colors.ink,
  },
  capsuleBadge: {
    backgroundColor: colors.accentSoft,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.small,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: typography.tiny.fontSize,
    fontWeight: "700",
    color: colors.accent,
  },
});
