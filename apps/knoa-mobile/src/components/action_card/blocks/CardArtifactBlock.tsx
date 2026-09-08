import React from "react";
import { Linking, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { colors, radii, spacing, typography } from "@/theme";
import type { CardArtifactBlock as CardArtifactBlockType } from "../types";

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

export function CardArtifactBlock({ block }: { block: CardArtifactBlockType }) {
  const handlePress = () => {
    if (block.url) {
      Linking.openURL(block.url).catch(() => undefined);
    }
  };

  const sizeText = formatBytes(block.size_bytes);

  return (
    <AppPressable style={styles.container} onPress={handlePress}>
      <View style={styles.iconWrapper}>
        <AppIcon name="file" size={18} color={colors.accent} />
      </View>
      <View style={styles.content}>
        <Text style={styles.name} numberOfLines={1}>
          {block.name}
        </Text>
        {sizeText || block.mime_type ? (
          <Text style={styles.meta}>
            {[sizeText, block.mime_type].filter(Boolean).join(" · ")}
          </Text>
        ) : null}
      </View>
      <AppIcon name="arrow-down" size={16} color={colors.muted} />
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.medium,
    marginVertical: spacing.xsmall,
    gap: spacing.medium,
  },
  iconWrapper: {
    width: 36,
    height: 36,
    borderRadius: radii.small,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  content: {
    flex: 1,
    gap: 2,
  },
  name: {
    fontSize: typography.body.fontSize,
    fontWeight: "600",
    color: colors.ink,
  },
  meta: {
    fontSize: typography.caption.fontSize,
    color: colors.muted,
  },
});
