import { memo } from "react";
import { StyleSheet, Text, View } from "react-native";

import { useI18n } from "../../i18n";
import { colors, radii, shadows, spacing, typography } from "../../theme";
import { AppIcon } from "../AppIcon";
import { AppPressable } from "../AppPressable";
import { DECK_ACTIONS, type ActionItem } from "./proactiveDeckModel";

export interface ProactiveDeckProps {
  computerName?: string;
  toolCount?: number;
  modelName?: string;
  isOnline?: boolean;
  onSelectPrompt: (prompt: string, autoSend?: boolean) => void;
  onLaunchTask: (title: string, goal: string) => void;
}

export const ProactiveDeck = memo(function ProactiveDeck({
  computerName,
  toolCount,
  modelName,
  isOnline = true,
  onSelectPrompt,
  onLaunchTask,
}: ProactiveDeckProps) {
  const { t } = useI18n();

  return (
    <View style={styles.container}>
      {/* 顶部数字员工英雄横幅 */}
      <View
        style={[
          styles.heroCard,
          {
            backgroundColor: colors.surface,
            borderColor: colors.line,
            borderRadius: radii.large,
            padding: spacing.medium,
            ...shadows.card,
          },
        ]}
      >
        <View style={styles.heroHeader}>
          <View style={styles.heroTitleRow}>
            <View
              style={[
                styles.avatarWrap,
                {
                  backgroundColor: colors.accentSoft,
                  borderRadius: radii.pill,
                },
              ]}
            >
              <AppIcon name="agent" color={colors.accent} size={22} />
            </View>
            <View style={styles.heroTextCol}>
              <Text style={[styles.heroTitle, { color: colors.ink, ...typography.subheading }]}>
                {t("chat.deckTitle")}
              </Text>
              <Text style={[styles.heroSlogan, { color: colors.accent, ...typography.caption }]}>
                {t("chat.deckSlogan")}
              </Text>
            </View>
          </View>

          {/* 状态指示胶囊 */}
          <View
            style={[
              styles.statusPill,
              {
                backgroundColor: isOnline ? colors.accentSoft : colors.surfaceMuted,
                borderColor: isOnline ? colors.accent : colors.line,
                borderRadius: radii.pill,
              },
            ]}
          >
            <View
              style={[
                styles.statusDot,
                { backgroundColor: isOnline ? colors.accent : colors.muted },
              ]}
            />
            <Text
              style={[
                styles.statusText,
                { color: isOnline ? colors.accent : colors.muted, ...typography.tiny },
              ]}
              numberOfLines={1}
            >
              {computerName || (isOnline ? t("workspace.online") : t("workspace.offline"))}
            </Text>
          </View>
        </View>

        <Text style={[styles.heroSubtitle, { color: colors.muted, ...typography.small }]}>
          {t("chat.deckSubtitle")}
        </Text>

        {/* 算力与能力标签行 */}
        <View style={styles.badgesRow}>
          {typeof toolCount === "number" && toolCount > 0 ? (
            <View
              style={[
                styles.metaBadge,
                { backgroundColor: colors.surfaceMuted, borderRadius: radii.small },
              ]}
            >
              <AppIcon name="node" color={colors.accent} size={12} />
              <Text style={[styles.metaBadgeText, { color: colors.ink, ...typography.tiny }]}>
                {t("chat.deckToolsReady", { count: toolCount })}
              </Text>
            </View>
          ) : null}

          {modelName ? (
            <View
              style={[
                styles.metaBadge,
                { backgroundColor: colors.surfaceMuted, borderRadius: radii.small },
              ]}
            >
              <AppIcon name="desktop" color={colors.accent} size={12} />
              <Text style={[styles.metaBadgeText, { color: colors.ink, ...typography.tiny }]}>
                {modelName}
              </Text>
            </View>
          ) : null}
        </View>
      </View>

      {/* Bento 行动卡片流 */}
      <View style={styles.actionsGrid}>
        {DECK_ACTIONS.map((item) => (
          <View
            key={item.key}
            style={[
              styles.actionCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.line,
                borderRadius: radii.medium,
                padding: spacing.medium,
                ...shadows.card,
              },
            ]}
          >
            <AppPressable
              style={styles.cardHeader}
              onPress={() => onSelectPrompt(item.prompt, false)}
            >
              <View
                style={[
                  styles.iconWrap,
                  {
                    backgroundColor: colors.accentSoft,
                    borderRadius: radii.small,
                  },
                ]}
              >
                <AppIcon name={item.icon} color={colors.accent} size={18} />
              </View>
              <View style={styles.cardTextWrap}>
                <Text style={[styles.cardTitle, { color: colors.ink, ...typography.body }]}>
                  {t(item.titleKey)}
                </Text>
                <Text
                  style={[styles.cardDesc, { color: colors.muted, ...typography.small }]}
                  numberOfLines={2}
                >
                  {t(item.descKey)}
                </Text>
              </View>
            </AppPressable>

            {/* 快捷操作条：即时执行 / 派发任务 */}
            <View style={[styles.buttonRow, { borderTopColor: colors.line }]}>
              <AppPressable
                style={[
                  styles.quickActionBtn,
                  styles.quickActionBtnPrimary,
                  { backgroundColor: colors.accentSoft, borderRadius: radii.small },
                ]}
                onPress={() => onSelectPrompt(item.prompt, true)}
              >
                <AppIcon name="send" color={colors.accent} size={12} />
                <Text style={[styles.quickActionTextPrimary, { color: colors.accent, ...typography.tiny }]}>
                  {t("chat.deckDirectChat")}
                </Text>
              </AppPressable>

              <AppPressable
                style={[
                  styles.quickActionBtn,
                  { backgroundColor: colors.surfaceMuted, borderRadius: radii.small },
                ]}
                onPress={() => onLaunchTask(item.taskTitle, item.prompt)}
              >
                <AppIcon name="tasks" color={colors.ink} size={12} />
                <Text style={[styles.quickActionText, { color: colors.ink, ...typography.tiny }]}>
                  {t("chat.deckDirectTask")}
                </Text>
              </AppPressable>
            </View>
          </View>
        ))}
      </View>

      {/* 底部常见速问提示 */}
      <View style={styles.faqSection}>
        <Text style={[styles.faqSectionTitle, { color: colors.muted, ...typography.caption }]}>
          {t("chat.deckFaqTitle")}
        </Text>
        <View style={styles.faqPills}>
          {[
            t("chat.exampleGitLab"),
            t("chat.exampleJira"),
            t("chat.exampleImage"),
            t("chat.exampleCode"),
          ].map((example) => (
            <AppPressable
              key={example}
              style={[
                styles.faqPill,
                {
                  backgroundColor: colors.surface,
                  borderColor: colors.line,
                  borderRadius: radii.pill,
                },
              ]}
              onPress={() => onSelectPrompt(example, false)}
            >
              <Text style={[styles.faqPillText, { color: colors.ink, ...typography.tiny }]}>
                {example}
              </Text>
            </AppPressable>
          ))}
        </View>
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  container: {
    width: "100%",
    paddingTop: 4,
    paddingBottom: 24,
  },
  heroCard: {
    borderWidth: 1,
    marginBottom: 16,
  },
  heroHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  heroTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    flex: 1,
  },
  avatarWrap: {
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
  },
  heroTextCol: {
    flex: 1,
  },
  heroTitle: {
    fontWeight: "700",
  },
  heroSlogan: {
    fontWeight: "600",
    marginTop: 2,
  },
  statusPill: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderWidth: 1,
    gap: 5,
    maxWidth: 120,
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  statusText: {
    fontWeight: "600",
  },
  heroSubtitle: {
    lineHeight: 18,
    marginBottom: 10,
  },
  badgesRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  metaBadge: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 4,
    gap: 5,
  },
  metaBadgeText: {
    fontWeight: "500",
  },
  actionsGrid: {
    gap: 12,
    marginBottom: 20,
  },
  actionCard: {
    borderWidth: 1,
  },
  cardHeader: {
    flexDirection: "row",
    gap: 12,
    marginBottom: 12,
  },
  iconWrap: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
  },
  cardTextWrap: {
    flex: 1,
  },
  cardTitle: {
    fontWeight: "600",
    marginBottom: 4,
  },
  cardDesc: {
    lineHeight: 16,
  },
  buttonRow: {
    flexDirection: "row",
    gap: 10,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  quickActionBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 8,
    gap: 6,
  },
  quickActionBtnPrimary: {},
  quickActionText: {
    fontWeight: "500",
  },
  quickActionTextPrimary: {
    fontWeight: "600",
  },
  faqSection: {
    marginTop: 4,
  },
  faqSectionTitle: {
    marginBottom: 8,
    fontWeight: "600",
  },
  faqPills: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  faqPill: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderWidth: 1,
  },
  faqPillText: {},
});
