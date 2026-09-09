import React, { useState } from "react";
import { type ColorValue, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { colors, radii, spacing, typography } from "@/theme";
import type { CardCodeDiffBlock as CardCodeDiffBlockType } from "../types";

const MAX_COLLAPSED_LINES = 15;

export function CardCodeDiffBlock({ block }: { block: CardCodeDiffBlockType }) {
  const [expanded, setExpanded] = useState(false);

  const lines = (block.unified_diff || "").split("\n");
  const shouldCollapse = lines.length > MAX_COLLAPSED_LINES && !expanded;
  const visibleLines = shouldCollapse ? lines.slice(0, MAX_COLLAPSED_LINES) : lines;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <AppIcon name="code" size={14} color={colors.muted} />
          <Text style={styles.filename} numberOfLines={1}>
            {block.filename}
          </Text>
        </View>
        {block.language ? (
          <View style={styles.langBadge}>
            <Text style={styles.langText}>{block.language.toUpperCase()}</Text>
          </View>
        ) : null}
      </View>

      <View style={styles.diffBody}>
        {visibleLines.map((line, idx) => {
          const isAdd = line.startsWith("+") && !line.startsWith("+++");
          const isDel = line.startsWith("-") && !line.startsWith("---");
          const isHunk = line.startsWith("@@");

          let lineBg: ColorValue = "transparent";
          let textColor: ColorValue = colors.ink;

          if (isAdd) {
            lineBg = colors.successSoft;
            textColor = colors.success;
          } else if (isDel) {
            lineBg = colors.dangerSoft;
            textColor = colors.danger;
          } else if (isHunk) {
            lineBg = colors.surfaceMuted;
            textColor = colors.muted;
          }

          return (
            <View key={idx} style={[styles.diffLine, { backgroundColor: lineBg }]}>
              <Text style={[styles.codeText, { color: textColor }]}>
                {line || " "}
              </Text>
            </View>
          );
        })}
      </View>

      {shouldCollapse ? (
        <AppPressable
          style={styles.expandButton}
          onPress={() => setExpanded(true)}
        >
          <Text style={styles.expandText}>
            展开查看全部 (+{lines.length - MAX_COLLAPSED_LINES} 行)
          </Text>
          <AppIcon name="chevron-down" size={14} color={colors.accent} />
        </AppPressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: "hidden",
    marginVertical: spacing.xsmall,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.surfaceMuted,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  headerLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    flex: 1,
    marginRight: spacing.small,
  },
  filename: {
    fontSize: typography.caption.fontSize,
    fontWeight: "700",
    color: colors.ink,
    fontFamily: "monospace",
  },
  langBadge: {
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.small,
    paddingVertical: 1,
    borderRadius: radii.small,
    borderWidth: 1,
    borderColor: colors.line,
  },
  langText: {
    fontSize: typography.tiny.fontSize,
    fontWeight: "600",
    color: colors.muted,
  },
  diffBody: {
    paddingVertical: 2,
  },
  diffLine: {
    paddingHorizontal: spacing.medium,
    paddingVertical: 1,
  },
  codeText: {
    fontSize: 12,
    fontFamily: "monospace",
    lineHeight: 18,
  },
  expandButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    paddingVertical: spacing.small,
    backgroundColor: colors.surfaceMuted,
    borderTopWidth: 1,
    borderTopColor: colors.line,
  },
  expandText: {
    fontSize: typography.caption.fontSize,
    fontWeight: "600",
    color: colors.accent,
  },
});
