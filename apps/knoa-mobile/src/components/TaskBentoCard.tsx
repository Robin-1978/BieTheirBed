import React from "react";
import { ActivityIndicator, Image, StyleSheet, Text, View } from "react-native";

import type { Task, TaskDefinitionState, TaskState } from "@/api/models";
import { useI18n } from "@/i18n";
import { colors, radii, shadows, spacing, typography } from "@/theme";
import { AppIcon } from "./AppIcon";
import { AppPressable } from "./AppPressable";
import {
  bentoProgressStep,
  estimateSavedMinutes,
  taskBentoCategory,
  type DesktopGlanceRecord,
} from "./taskBentoPresentation";

export interface TaskBentoCardProps {
  task: Task;
  unread?: boolean;
  isExecuting?: boolean;
  glanceRecord?: DesktopGlanceRecord | null;
  onPress: (task: Task) => void;
  onExecute: (taskId: string) => void;
  onTogglePause: (task: Task) => void;
  onOpenExecution?: (executionId: string) => void;
  onPressGlance?: (record: DesktopGlanceRecord) => void;
  onSteer?: (task: Task) => void;
}

export function TaskBentoCard({
  task,
  unread = false,
  isExecuting = false,
  glanceRecord,
  onPress,
  onExecute,
  onTogglePause,
  onOpenExecution,
  onPressGlance,
  onSteer,
}: TaskBentoCardProps) {
  const { t } = useI18n();
  const category = taskBentoCategory(task);
  const savedMinutes = estimateSavedMinutes(task);
  const progressText = bentoProgressStep(task, t("tasks.runningIndicator"));

  return (
    <AppPressable
      accessibilityRole="button"
      style={[
        styles.card,
        category === "needs_action" && styles.cardNeedsAction,
        category === "running" && styles.cardRunning,
        category === "completed" && styles.cardCompleted,
      ]}
      onPress={() => onPress(task)}
    >
      {/* 顶部 Bento 状态胶囊条 */}
      <View style={styles.topStatusRow}>
        {category === "needs_action" ? (
          <View style={[styles.pillBadge, styles.pillBadgeWarning]}>
            <AppIcon name="alert" color={colors.warning} size={13} />
            <Text style={[styles.pillText, styles.pillTextWarning]}>
              {t("tasks.bentoActionRequired")}
              {task.pending_approval_count > 0 ? ` (${task.pending_approval_count})` : ""}
            </Text>
          </View>
        ) : category === "running" ? (
          <View style={[styles.pillBadge, styles.pillBadgeAccent]}>
            <AppIcon name="pulse" color={colors.accent} size={13} />
            <Text style={[styles.pillText, styles.pillTextAccent]}>
              {t("tasks.bentoRunning")}
            </Text>
          </View>
        ) : category === "completed" ? (
          <View style={styles.completedHeaderRow}>
            <View style={[styles.pillBadge, styles.pillBadgeSuccess]}>
              <AppIcon name="check" color={colors.accent} size={13} />
              <Text style={[styles.pillText, styles.pillTextSuccess]}>
                {t("tasks.bentoCompleted")}
              </Text>
            </View>
            {savedMinutes > 0 ? (
              <View style={styles.savedPill}>
                <AppIcon name="pulse" color={colors.accent} size={11} />
                <Text style={styles.savedPillText}>
                  {t("tasks.bentoSaved", { minutes: savedMinutes })}
                </Text>
              </View>
            ) : null}
          </View>
        ) : (
          <View style={[styles.pillBadge, styles.pillBadgeMuted]}>
            <Text style={[styles.pillText, styles.pillTextMuted]}>
              {taskStateLabel(task.state, t)}
            </Text>
          </View>
        )}

        <View style={styles.metaRow}>
          <Text style={styles.metaSubtext}>{launchLabel(task, t)}</Text>
          <Text style={styles.metaDivider}>·</Text>
          <Text style={styles.metaSubtext}>
            {t("tasks.executions", { count: task.execution_count })}
          </Text>
        </View>
      </View>

      {/* 主标题与未读指示 */}
      <View style={styles.titleRow}>
        {unread ? <View style={styles.unreadDot} /> : null}
        <Text style={[styles.title, { color: colors.ink }]} numberOfLines={1}>
          {task.title}
        </Text>
      </View>

      {/* 狂飙中：桌面窥探视窗（Desktop Glance）或步骤流水线 */}
      {category === "running" ? (
        <View style={styles.glanceContainer}>
          {glanceRecord?.thumbnailBase64 ? (
            <AppPressable
              style={styles.glanceImageWrap}
              onPress={() => onPressGlance?.(glanceRecord)}
            >
              <Image
                source={{ uri: `data:image/jpeg;base64,${glanceRecord.thumbnailBase64}` }}
                style={styles.glanceThumbnail}
                resizeMode="cover"
              />
              <View style={styles.glanceOverlay}>
                <AppIcon name="desktop" color={colors.surface} size={11} />
                <Text style={styles.glanceOverlayText} numberOfLines={1}>
                  {glanceRecord.windowTitle || glanceRecord.activeApp || t("tasks.bentoGlance")}
                </Text>
              </View>
            </AppPressable>
          ) : (
            <View style={styles.pipelineWrap}>
              <View style={styles.pipelineHeader}>
                <View style={styles.pulseDot} />
                <Text style={styles.pipelineTitle}>{t("tasks.bentoPipeline")}</Text>
              </View>
              <Text style={styles.pipelineText} numberOfLines={2}>
                {progressText || task.goal}
              </Text>
            </View>
          )}
        </View>
      ) : null}

      {/* 待裁决：高危影响面警示摘要 */}
      {category === "needs_action" ? (
        <View style={styles.actionRequiredBox}>
          <Text style={styles.actionRequiredText} numberOfLines={2}>
            {task.latest_execution_summary || task.goal}
          </Text>
        </View>
      ) : null}

      {/* 今日收官或普通状态：成果摘要或任务目标 */}
      {category !== "running" && category !== "needs_action" ? (
        task.latest_execution_state === "failed" && task.latest_execution_failure_code ? (
          <Text style={styles.failure} numberOfLines={2}>
            {t("tasks.latestFailure", { code: task.latest_execution_failure_code })}
          </Text>
        ) : task.latest_execution_summary ? (
          <View style={styles.latestResult}>
            <Text style={styles.resultText} numberOfLines={2}>
              {task.latest_execution_summary}
            </Text>
          </View>
        ) : (
          <Text style={styles.goal} numberOfLines={2}>{task.goal}</Text>
        )
      ) : null}

      {/* 底部快捷操作条（就地秒级控制） */}
      <View style={styles.cardFooter}>
        <View style={styles.actionLeft}>
          {task.state !== "archived" ? (
            <AppPressable
              style={styles.quickButton}
              onPress={() => onTogglePause(task)}
            >
              <AppIcon
                name={task.state === "active" ? "pause" : "play"}
                color={colors.muted}
                size={12}
              />
              <Text style={styles.quickButtonText}>
                {task.state === "active" ? t("tasks.swipePause") : t("tasks.swipeResume")}
              </Text>
            </AppPressable>
          ) : null}

          {category === "running" && onSteer ? (
            <AppPressable
              style={[styles.quickButton, styles.quickSteerButton]}
              onPress={() => onSteer(task)}
            >
              <AppIcon name="agent" color={colors.accent} size={12} />
              <Text style={[styles.quickButtonText, styles.quickSteerButtonText]}>
                {t("tasks.steerAction")}
              </Text>
            </AppPressable>
          ) : null}

          {task.latest_execution_id && onOpenExecution ? (
            <AppPressable
              style={styles.quickButton}
              onPress={() => onOpenExecution(task.latest_execution_id)}
            >
              <Text style={styles.quickButtonText}>
                {category === "completed" ? t("tasks.bentoArtifacts") : t("results.openExecution")}
              </Text>
            </AppPressable>
          ) : null}
        </View>

        <View style={styles.actionRight}>
          {category === "needs_action" ? (
            <AppPressable
              style={[styles.primaryActionBtn, styles.primaryActionWarning]}
              onPress={() => onPress(task)}
            >
              <AppIcon name="alert" color={colors.surface} size={12} />
              <Text style={styles.primaryActionBtnText}>{t("tasks.bentoReview")}</Text>
            </AppPressable>
          ) : (
            <AppPressable
              disabled={isExecuting}
              style={[styles.primaryActionBtn, styles.primaryActionNormal]}
              onPress={() => onExecute(task.task_id)}
            >
              {isExecuting ? (
                <ActivityIndicator color={colors.onAccent} size="small" />
              ) : (
                <>
                  <AppIcon name="play" color={colors.onAccent} size={12} />
                  <Text style={styles.primaryActionBtnText}>
                    {category === "completed" ? t("tasks.bentoRerun") : t("taskDetail.executeNow")}
                  </Text>
                </>
              )}
            </AppPressable>
          )}
        </View>
      </View>
    </AppPressable>
  );
}

function taskStateLabel(state: TaskDefinitionState, t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    active: t("tasks.state.active"),
    paused: t("tasks.state.paused"),
    archived: t("tasks.state.archived"),
  })[state] || state;
}

function launchLabel(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  switch (task.launch_policy?.kind) {
    case "scheduled":
      return t("tasks.launch.scheduled");
    case "event":
      return t("tasks.launch.event");
    default:
      return t("tasks.launch.manual");
  }
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.large,
    gap: spacing.medium,
    ...shadows.card,
  },
  cardNeedsAction: {
    borderColor: colors.warning,
    borderWidth: 1.5,
    backgroundColor: colors.surface,
  },
  cardRunning: {
    borderColor: colors.accent,
    borderWidth: 1.5,
    backgroundColor: colors.surface,
  },
  cardCompleted: {
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  topStatusRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  completedHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  pillBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radii.pill,
  },
  pillText: {
    fontSize: 11,
    fontWeight: "700",
  },
  pillBadgeWarning: {
    backgroundColor: colors.warningSoft,
  },
  pillTextWarning: {
    color: colors.warning,
  },
  pillBadgeAccent: {
    backgroundColor: colors.accentSoft,
  },
  pillTextAccent: {
    color: colors.accent,
  },
  pillBadgeSuccess: {
    backgroundColor: colors.accentSoft,
  },
  pillTextSuccess: {
    color: colors.accent,
  },
  pillBadgeMuted: {
    backgroundColor: colors.surfaceMuted,
  },
  pillTextMuted: {
    color: colors.muted,
    fontWeight: "600",
  },
  savedPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    backgroundColor: colors.accentSoft,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radii.small,
  },
  savedPillText: {
    color: colors.accent,
    fontSize: 10,
    fontWeight: "700",
  },
  metaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  metaSubtext: {
    color: colors.muted,
    fontSize: 11,
  },
  metaDivider: {
    color: colors.muted,
    fontSize: 10,
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  unreadDot: {
    width: 7,
    height: 7,
    borderRadius: 3.5,
    backgroundColor: colors.accent,
  },
  title: {
    fontSize: 16,
    fontWeight: "700",
    flex: 1,
  },
  glanceContainer: {
    borderRadius: radii.medium,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surfaceMuted,
  },
  glanceImageWrap: {
    width: "100%",
    height: 120,
    position: "relative",
  },
  glanceThumbnail: {
    width: "100%",
    height: "100%",
  },
  glanceOverlay: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: "rgba(0, 0, 0, 0.6)",
    paddingHorizontal: 8,
    paddingVertical: 4,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  glanceOverlayText: {
    color: colors.surface,
    fontSize: 11,
    fontWeight: "600",
    flex: 1,
  },
  pipelineWrap: {
    padding: spacing.medium,
    gap: 4,
  },
  pipelineHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  pulseDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.accent,
  },
  pipelineTitle: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
  },
  pipelineText: {
    color: colors.ink,
    fontSize: 13,
    lineHeight: 18,
  },
  actionRequiredBox: {
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.warningSoft || "#fef3c7",
    borderLeftWidth: 3,
    borderLeftColor: colors.warning,
  },
  actionRequiredText: {
    color: colors.ink,
    fontSize: 12,
    lineHeight: 17,
    fontWeight: "500",
  },
  goal: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 18,
  },
  latestResult: {
    backgroundColor: colors.surfaceMuted,
    padding: spacing.small,
    borderRadius: radii.small,
  },
  resultText: {
    color: colors.ink,
    fontSize: 12,
    lineHeight: 17,
  },
  failure: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "500",
  },
  cardFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: spacing.small,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
  },
  actionLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  actionRight: {
    flexDirection: "row",
    alignItems: "center",
  },
  quickButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: radii.small,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  quickSteerButton: {
    borderColor: colors.accent,
    backgroundColor: colors.accentSoft,
  },
  quickButtonText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600",
  },
  quickSteerButtonText: {
    color: colors.accent,
    fontWeight: "700",
  },
  primaryActionBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radii.medium,
  },
  primaryActionNormal: {
    backgroundColor: colors.accent,
  },
  primaryActionWarning: {
    backgroundColor: colors.warning,
  },
  primaryActionBtnText: {
    color: colors.onAccent,
    fontSize: 12,
    fontWeight: "700",
  },
});
