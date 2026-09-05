import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as Clipboard from "expo-clipboard";
import { File, Paths } from "expo-file-system";

import type { Task } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { assistantArtifactItems, resolveAssistantArtifactFile, type ResolvedArtifactFile } from "@/api/chatArtifacts";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { shareResultJson, shareResultPdf, shareResultText } from "@/api/shareResult";
import { resultOutcome } from "@/components/resultSummaryPresentation";
import {
  calculateTotalSavedHours,
  classifyArtifactType,
  hostRelativePath,
} from "@/components/trophyPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { loadTaskCache, storeTaskCache } from "@/storage/taskCache";
import { colors, radii, spacing, shadows, typography } from "@/theme";

type AssetFilter = "all" | "tasks" | "artifacts";

export default function UnifiedAssetsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();

  const [activeFilter, setActiveFilter] = useState<AssetFilter>("all");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [artifacts, setArtifacts] = useState<Array<{ artifact_id: string; name: string; media_type: string; size: number; kind: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [sharing, setSharing] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [feedbackMessage, setFeedbackMessage] = useState("");
  const [previewFile, setPreviewFile] = useState<ResolvedArtifactFile | null>(null);

  const taskCacheScope = params.nodeId?.trim() || gateway.nodeId || "unselected";

  const refresh = useCallback(async (manual = false) => {
    if (!gateway.client) {
      setLoading(false);
      setError(gateway.status === "error" ? t("results.loadFailed") : t("chat.reconnecting"));
      setRefreshing(false);
      return;
    }
    if (manual) setRefreshing(true);
    setError("");

    try {
      const [taskRes, artifactRes] = await Promise.all([
        gateway.runAuthenticated((client) => client.listTasks({ includeArchived: true, limit: 100 })),
        gateway.sessionHandle
          ? gateway.runAuthenticated((client) => client.searchArtifacts({ sessionHandle: gateway.sessionHandle || "" }))
          : Promise.resolve({ artifacts: [] }),
      ]);
      setTasks(taskRes.tasks);
      setArtifacts(artifactRes.artifacts);
      void storeTaskCache(taskCacheScope, taskRes.tasks);
    } catch {
      setError(t("results.loadFailed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [gateway.client, gateway.runAuthenticated, gateway.sessionHandle, gateway.status, t, taskCacheScope]);

  useEffect(() => {
    let active = true;
    void loadTaskCache(taskCacheScope).then((cached) => {
      if (!active || !cached) return;
      setTasks(cached);
      setLoading(false);
    }).finally(() => {
      if (active) void refresh();
    });
    return () => { active = false; };
  }, [refresh, taskCacheScope]);

  const taskResults = useMemo(
    () => tasks
      .filter((task) => Boolean(task.latest_execution_id || task.latest_execution_summary || task.latest_execution_failure_code))
      .sort((left, right) => (right.latest_execution_updated_at ?? right.updated_at) - (left.latest_execution_updated_at ?? left.updated_at)),
    [tasks],
  );

  async function openArtifactPreview(item: (typeof artifacts)[number], saveOnly = false) {
    if (!gateway.sessionHandle) return;
    try {
      const artifactItem = assistantArtifactItems([item])[0];
      if (!artifactItem) return;
      const isImage = item.kind === "image" || item.media_type.startsWith("image/");
      const resolved = await resolveAssistantArtifactFile(
        {
          artifact: item,
          key: item.artifact_id,
          displayName: item.name,
          cacheFileName: artifactItem.cacheFileName,
          isImage,
        },
        {
          cachedUri: (name) => {
            const file = new File(Paths.document, `artifact-${name}`);
            return file.exists ? file.uri : null;
          },
          download: (artifactId) => gateway.runAuthenticated((client) => client.downloadArtifact(gateway.sessionHandle || "", artifactId)),
          write: (name, bytes) => {
            const file = new File(Paths.document, `artifact-${name}`);
            file.create({ overwrite: true, intermediates: true });
            file.write(bytes);
            return file.uri;
          },
        },
      );

      if (saveOnly) {
        await saveArtifactFile(resolved);
      } else if (isImage) {
        setPreviewFile(resolved);
      } else {
        await saveArtifactFile(resolved);
      }
    } catch {
      setError(t("artifacts.loadFailed"));
    }
  }

  const totalSavedHours = useMemo(() => calculateTotalSavedHours(tasks), [tasks]);

  const copyHostPath = useCallback(async (fileName: string, artifactId: string) => {
    const path = hostRelativePath(fileName, artifactId);
    await Clipboard.setStringAsync(path);
    setFeedbackMessage(t("trophy.hostPathCopied"));
    setTimeout(() => setFeedbackMessage(""), 2000);
  }, [t]);

  const normalizedQuery = searchQuery.trim().toLowerCase();

  const filteredTaskResults = useMemo(
    () => taskResults.filter((task) => {
      if (!normalizedQuery) return true;
      return (
        task.title.toLowerCase().includes(normalizedQuery) ||
        (task.latest_execution_summary && task.latest_execution_summary.toLowerCase().includes(normalizedQuery))
      );
    }),
    [normalizedQuery, taskResults],
  );

  const filteredArtifacts = useMemo(
    () => artifacts.filter((artifact) => {
      if (!normalizedQuery) return true;
      return (
        artifact.name.toLowerCase().includes(normalizedQuery) ||
        artifact.media_type.toLowerCase().includes(normalizedQuery)
      );
    }),
    [artifacts, normalizedQuery],
  );

  const hasData = (activeFilter === "all" && (filteredTaskResults.length > 0 || filteredArtifacts.length > 0))
    || (activeFilter === "tasks" && filteredTaskResults.length > 0)
    || (activeFilter === "artifacts" && filteredArtifacts.length > 0);

  const totalDeliverables = filteredTaskResults.length + filteredArtifacts.length;

  return (
    <>
      <ScrollView
        style={styles.screen}
        contentContainerStyle={styles.container}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh(true)} />}
      >
        {/* 数字战果陈列室 Hero 标头 */}
        <View style={styles.trophyHero}>
          <View style={styles.trophyHeroIcon}>
            <AppIcon name="archive" color={colors.accent} size={22} />
          </View>
          <View style={styles.trophyHeroContent}>
            <Text style={styles.trophyHeroTitle}>{t("trophy.heroTitle")}</Text>
            <Text style={styles.trophyHeroSubtitle}>{t("trophy.heroSubtitle")}</Text>
            <View style={styles.trophyStatBadge}>
              <AppIcon name="pulse" color={colors.accent} size={12} />
              <Text style={styles.trophyStatText}>
                {t("trophy.statSummary", { hours: totalSavedHours, count: totalDeliverables })}
              </Text>
            </View>
          </View>
        </View>

        {feedbackMessage ? (
          <View style={styles.feedbackToast}>
            <AppIcon name="check" color={colors.accent} size={14} />
            <Text style={styles.feedbackToastText}>{feedbackMessage}</Text>
          </View>
        ) : null}

        {/* 全文搜索输入条 */}
        <View style={styles.searchBar}>
          <AppIcon name="more" color={colors.muted} size={16} />
          <TextInput
            style={styles.searchInput}
            value={searchQuery}
            onChangeText={setSearchQuery}
            placeholder={t("assets.searchPlaceholder")}
            placeholderTextColor={colors.muted}
            clearButtonMode="while-editing"
          />
          {searchQuery ? (
            <AppPressable onPress={() => setSearchQuery("")} style={styles.clearSearch}>
              <AppIcon name="x" color={colors.muted} size={14} />
            </AppPressable>
          ) : null}
        </View>

        <View style={styles.segmentContainer}>
          <AppPressable
            style={[styles.segmentItem, activeFilter === "all" && styles.segmentActive]}
            onPress={() => setActiveFilter("all")}
          >
            <Text style={[styles.segmentText, activeFilter === "all" && styles.segmentTextActive]}>
              {t("assets.filterAll")}
            </Text>
          </AppPressable>
          <AppPressable
            style={[styles.segmentItem, activeFilter === "tasks" && styles.segmentActive]}
            onPress={() => setActiveFilter("tasks")}
          >
            <Text style={[styles.segmentText, activeFilter === "tasks" && styles.segmentTextActive]}>
              {t("assets.filterTasks")} ({filteredTaskResults.length})
            </Text>
          </AppPressable>
          <AppPressable
            style={[styles.segmentItem, activeFilter === "artifacts" && styles.segmentActive]}
            onPress={() => setActiveFilter("artifacts")}
          >
            <Text style={[styles.segmentText, activeFilter === "artifacts" && styles.segmentTextActive]}>
              {t("assets.filterArtifacts")} ({filteredArtifacts.length})
            </Text>
          </AppPressable>
        </View>

        {loading ? <AsyncStateView state="loading" /> : null}
        {error && !loading ? (
          <AsyncStateView state="error" message={error} retryLabel={t("common.refresh")} onRetry={() => void refresh(true)} />
        ) : null}
        {!loading && !error && !hasData ? (
          <AsyncStateView state="empty" title={t("results.emptyTitle")} message={t("results.emptyDetail")} />
        ) : null}

        {/* 任务成果列表 */}
        {(activeFilter === "all" || activeFilter === "tasks") && filteredTaskResults.map((task) => {
          const outcome = resultOutcome(task);
          const stateLabel = resultState(task, t);
          return (
            <View key={task.task_id} style={styles.card}>
              <View style={styles.cardHeader}>
                <View style={styles.flex}>
                  <Text style={styles.cardTitle} numberOfLines={2}>{task.title}</Text>
                  <Text style={styles.meta}>
                    {new Date((task.latest_execution_updated_at ?? task.updated_at) * 1000).toLocaleString()}
                  </Text>
                </View>
                <View style={[styles.statusBadge, outcome.incomplete ? styles.badgeFail : styles.badgeSuccess]}>
                  <Text style={[styles.statusBadgeText, outcome.incomplete ? styles.textFail : styles.textSuccess]}>
                    {outcome.incomplete ? t("results.failure", { code: outcome.failureCode || "failed" }) : stateLabel}
                  </Text>
                </View>
              </View>

              {task.latest_execution_summary ? (
                <Text style={styles.resultText} numberOfLines={4}>{task.latest_execution_summary}</Text>
              ) : null}

              {/* 远端真机交付路径 */}
              <AppPressable
                style={styles.hostPathChip}
                onPress={() => void copyHostPath(`${task.title}.pdf`, task.task_id)}
              >
                <AppIcon name="file" color={colors.accent} size={12} />
                <Text style={styles.hostPathText} numberOfLines={1}>
                  {hostRelativePath(`${task.title}.pdf`, task.task_id)}
                </Text>
                <Text style={styles.copyPathHint}>{t("trophy.copyHostPath")}</Text>
              </AppPressable>

              <View style={styles.cardActions}>
                {task.latest_execution_id ? (
                  <AppPressable
                    style={styles.primaryAction}
                    onPress={() => router.push(`/task-executions/${task.latest_execution_id}`)}
                  >
                    <Text style={styles.primaryActionText}>{t("results.openExecution")}</Text>
                  </AppPressable>
                ) : null}
                <AppPressable
                  style={styles.secondaryAction}
                  onPress={() => router.push({ pathname: `/tasks/${task.task_id}`, params })}
                >
                  <Text style={styles.secondaryActionText}>{t("results.openTask")}</Text>
                </AppPressable>
                {task.latest_execution_summary ? (
                  <AppPressable
                    style={styles.secondaryAction}
                    disabled={sharing === task.task_id}
                    onPress={async () => {
                      setSharing(task.task_id);
                      try {
                        await shareResultPdf(task.title, `${task.title}\n\n${task.latest_execution_summary}`);
                      } catch {
                        setError(t("results.shareFailed"));
                      } finally {
                        setSharing("");
                      }
                    }}
                  >
                    <Text style={styles.secondaryActionText}>
                      {sharing === task.task_id ? t("results.sharing") : t("results.sharePdf")}
                    </Text>
                  </AppPressable>
                ) : null}
              </View>
            </View>
          );
        })}

        {/* 会话生成工件列表 */}
        {(activeFilter === "all" || activeFilter === "artifacts") && filteredArtifacts.map((artifact) => {
          const typeInfo = classifyArtifactType(artifact.name, artifact.media_type, artifact.kind);
          return (
            <View key={artifact.artifact_id} style={styles.card}>
              <View style={styles.cardHeader}>
                <View style={styles.artifactIconWrap}>
                  <AppIcon name={typeInfo.icon} color={colors.accent} size={22} />
                </View>
                <View style={styles.flex}>
                  <View style={styles.titleRow}>
                    <Text style={styles.cardTitle} numberOfLines={1}>{artifact.name}</Text>
                    <View style={styles.typeBadge}>
                      <Text style={styles.typeBadgeText}>{typeInfo.label}</Text>
                    </View>
                  </View>
                  <Text style={styles.meta}>
                    {artifact.media_type} · {(artifact.size / 1024).toFixed(1)} KB
                  </Text>
                </View>
              </View>

              {/* 远端真机交付路径 */}
              <AppPressable
                style={styles.hostPathChip}
                onPress={() => void copyHostPath(artifact.name, artifact.artifact_id)}
              >
                <AppIcon name="file" color={colors.accent} size={12} />
                <Text style={styles.hostPathText} numberOfLines={1}>
                  {hostRelativePath(artifact.name, artifact.artifact_id)}
                </Text>
                <Text style={styles.copyPathHint}>{t("trophy.copyHostPath")}</Text>
              </AppPressable>

              <View style={styles.cardActions}>
                <AppPressable
                  style={styles.primaryAction}
                  onPress={() => void openArtifactPreview(artifact, false)}
                >
                  <Text style={styles.primaryActionText}>{t("artifacts.open")}</Text>
                </AppPressable>
                <AppPressable
                  style={styles.secondaryAction}
                  onPress={() => void openArtifactPreview(artifact, true)}
                >
                  <Text style={styles.secondaryActionText}>{t("artifacts.save")}</Text>
                </AppPressable>
              </View>
            </View>
          );
        })}
      </ScrollView>

      {previewFile ? (
        <ArtifactViewer
          file={previewFile}
          onClose={() => setPreviewFile(null)}
          onMessage={(msg) => setError(msg)}
        />
      ) : null}
    </>
  );
}

function resultState(task: Task, t: ReturnType<typeof useI18n>["t"]): string {
  if (task.work_status) {
    return ({
      queued: t("taskState.queued"),
      working: t("taskState.running"),
      waiting_for_you: t("taskState.waitingApproval"),
      completed: t("taskState.completed"),
      failed: t("taskState.failed"),
      paused: t("tasks.state.paused"),
      cancelled: t("taskState.cancelled"),
    } as const)[task.work_status.status];
  }
  if (task.latest_execution_state === "completed") return t("results.completed");
  if (task.latest_execution_state === "failed") return t("results.failed");
  if (task.latest_execution_state === "running") return t("results.running");
  return t("taskState.completed");
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  container: {
    padding: spacing.large,
    gap: spacing.medium,
    paddingBottom: 48,
  },
  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    paddingHorizontal: spacing.medium,
    minHeight: 44,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  searchInput: {
    flex: 1,
    color: colors.ink,
    fontSize: 14,
    paddingVertical: 8,
  },
  clearSearch: {
    padding: 4,
  },
  segmentContainer: {
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    padding: 4,
    borderWidth: 1,
    borderColor: colors.line,
  },
  segmentItem: {
    flex: 1,
    paddingVertical: spacing.small,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.medium,
  },
  segmentActive: {
    backgroundColor: colors.accent,
  },
  segmentText: {
    color: colors.muted,
    ...typography.small,
    fontWeight: "700",
  },
  segmentTextActive: {
    color: colors.onAccent,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.large,
    gap: spacing.medium,
    ...shadows.card,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
  },
  flex: {
    flex: 1,
    minWidth: 0,
  },
  cardTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "800",
  },
  meta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 2,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radii.small,
  },
  badgeSuccess: {
    backgroundColor: colors.accentSoft,
  },
  badgeFail: {
    backgroundColor: colors.dangerSoft,
  },
  statusBadgeText: {
    fontSize: 11,
    fontWeight: "800",
  },
  textSuccess: {
    color: colors.accent,
  },
  textFail: {
    color: colors.danger,
  },
  resultText: {
    color: colors.ink,
    fontSize: 13,
    lineHeight: 18,
  },
  cardActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    flexWrap: "wrap",
    marginTop: spacing.xsmall,
  },
  primaryAction: {
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
  },
  primaryActionText: {
    color: colors.onAccent,
    fontSize: 12,
    fontWeight: "800",
  },
  secondaryAction: {
    borderWidth: 1,
    borderColor: colors.line,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    backgroundColor: colors.surface,
  },
  secondaryActionText: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "700",
  },
  artifactIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accentSoft,
  },
  trophyHero: {
    flexDirection: "row",
    alignItems: "flex-start",
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    padding: spacing.medium,
    gap: spacing.medium,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  trophyHeroIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 2,
  },
  trophyHeroContent: {
    flex: 1,
  },
  trophyHeroTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.ink,
  },
  trophyHeroSubtitle: {
    fontSize: 12,
    color: colors.muted,
    lineHeight: 16,
    marginTop: 2,
  },
  trophyStatBadge: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 4,
    backgroundColor: colors.accentSoft,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radii.pill,
    marginTop: 6,
  },
  trophyStatText: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.accent,
  },
  feedbackToast: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.accentSoft,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    alignSelf: "center",
  },
  feedbackToastText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.accent,
  },
  hostPathChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radii.medium,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  hostPathText: {
    flex: 1,
    fontSize: 11,
    color: colors.muted,
    fontFamily: "monospace",
  },
  copyPathHint: {
    fontSize: 10,
    fontWeight: "700",
    color: colors.accent,
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
  },
  typeBadge: {
    backgroundColor: colors.accentSoft,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radii.small,
  },
  typeBadgeText: {
    fontSize: 10,
    fontWeight: "800",
    color: colors.accent,
  },
});
