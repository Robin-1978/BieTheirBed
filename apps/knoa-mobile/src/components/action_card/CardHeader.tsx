import React from "react";
import { StyleSheet, Text, View } from "react-native";

import type { AppIconName } from "@/components/AppIcon";
import { AppIcon } from "@/components/AppIcon";
import { colors, radii, spacing, typography } from "@/theme";
import type { ActionCardLevel, ActionCardSource, ActionCardStatus } from "./types";

export type CardHeaderProps = {
  title: string;
  subtitle?: string;
  level: ActionCardLevel;
  status: ActionCardStatus;
  source: ActionCardSource;
};

const LEVEL_CONFIG: Record<ActionCardLevel, { label: string; bg: string; text: string; icon: AppIconName }> = {
  info: { label: "INFO", bg: "#E0F2FE", text: "#0369A1", icon: "pulse" },
  success: { label: "DONE", bg: "#DCFCE7", text: "#15803D", icon: "check" },
  warning: { label: "WARN", bg: "#FEF3C7", text: "#B45309", icon: "alert" },
  critical: { label: "CRITICAL", bg: "#FEE2E2", text: "#B91C1C", icon: "alert" },
};

const STATUS_CONFIG: Record<ActionCardStatus, { label: string; bg: string; text: string } | null> = {
  pending: null,
  approved: { label: "已放行", bg: "#DCFCE7", text: "#166534" },
  executed: { label: "已执行", bg: "#DCFCE7", text: "#166534" },
  rejected: { label: "已取消", bg: "#F3F4F6", text: "#4B5563" },
  expired: { label: "已过期", bg: "#F3F4F6", text: "#9CA3AF" },
};

export function CardHeader({ title, subtitle, level, status, source }: CardHeaderProps) {
  const levelCfg = LEVEL_CONFIG[level] ?? LEVEL_CONFIG.info;
  const statusCfg = STATUS_CONFIG[status];

  const sourceLabel = [
    source.plugin_name,
    source.agent_name ? `· ${source.agent_name}` : "",
  ].filter(Boolean).join(" ");

  return (
    <View style={styles.container}>
      <View style={styles.metaRow}>
        <View style={styles.badgeGroup}>
          <View style={[styles.levelBadge, { backgroundColor: levelCfg.bg }]}>
            <AppIcon name={levelCfg.icon} size={13} color={levelCfg.text} />
            <Text style={[styles.levelText, { color: levelCfg.text }]}>{levelCfg.label}</Text>
          </View>
          {sourceLabel ? (
            <Text style={styles.sourceText}>{sourceLabel}</Text>
          ) : null}
        </View>

        {statusCfg ? (
          <View style={[styles.statusBadge, { backgroundColor: statusCfg.bg }]}>
            <Text style={[styles.statusText, { color: statusCfg.text }]}>{statusCfg.label}</Text>
          </View>
        ) : null}
      </View>

      <Text style={styles.title}>{title}</Text>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: spacing.xsmall,
  },
  metaRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 2,
  },
  badgeGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  levelBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.small,
    paddingVertical: 2,
    borderRadius: radii.pill,
  },
  levelText: {
    fontSize: typography.tiny.fontSize,
    fontWeight: "700",
    letterSpacing: 0.5,
  },
  sourceText: {
    fontSize: typography.caption.fontSize,
    color: colors.muted,
  },
  statusBadge: {
    paddingHorizontal: spacing.small,
    paddingVertical: 2,
    borderRadius: radii.small,
  },
  statusText: {
    fontSize: typography.tiny.fontSize,
    fontWeight: "600",
  },
  title: {
    fontSize: typography.heading.fontSize,
    fontWeight: typography.heading.fontWeight,
    color: colors.ink,
    lineHeight: 26,
  },
  subtitle: {
    fontSize: typography.caption.fontSize,
    color: colors.muted,
    lineHeight: 18,
  },
});
