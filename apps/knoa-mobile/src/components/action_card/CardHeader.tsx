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

const LEVEL_CONFIG: Record<ActionCardLevel, { label: string; bg: typeof colors.infoSoft; text: typeof colors.info; icon: AppIconName }> = {
  info: { label: "INFO", bg: colors.infoSoft, text: colors.info, icon: "pulse" },
  success: { label: "DONE", bg: colors.successSoft, text: colors.success, icon: "check" },
  warning: { label: "WARN", bg: colors.warningSoft, text: colors.warning, icon: "alert" },
  critical: { label: "CRITICAL", bg: colors.dangerSoft, text: colors.danger, icon: "alert" },
};

const STATUS_CONFIG: Record<ActionCardStatus, { label: string; bg: typeof colors.successSoft; text: typeof colors.success } | { label: string; bg: typeof colors.surfaceMuted; text: typeof colors.muted } | null> = {
  pending: null,
  approved: { label: "已放行", bg: colors.successSoft, text: colors.success },
  executed: { label: "已执行", bg: colors.successSoft, text: colors.success },
  rejected: { label: "已取消", bg: colors.surfaceMuted, text: colors.muted },
  expired: { label: "已过期", bg: colors.surfaceMuted, text: colors.muted },
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
