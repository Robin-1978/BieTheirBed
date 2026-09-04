import * as DocumentPicker from "expo-document-picker";
import * as Crypto from "expo-crypto";
import { router, useLocalSearchParams } from "expo-router";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { AgentSummary, ArtifactInput, ChatArtifact, HumanInteraction, Task, TaskApproval, TaskExecution } from "@/api/models";
import type { ResolvedArtifactFile } from "@/api/chatArtifacts";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { shouldRefreshExecution } from "@/api/taskEvents";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { AppIcon } from "@/components/AppIcon";
import { AppMarkdown } from "@/components/AppMarkdown";
import { ApprovalRequestDetails } from "@/components/ApprovalRequestDetails";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { InteractionCard } from "@/components/InteractionCard";
import { WorkResultSummary } from "@/components/WorkResultSummary";
import { mergeTaskTimeline, type TaskTimelineItem } from "@/components/taskTimeline";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { useTaskReminders } from "@/state/TaskReminderProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { loadExecutionCache, storeExecutionCache } from "@/storage/executionCache";

export default function TaskExecutionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const executionId = String(id ?? "");
  const gateway = useGateway();
  const { setExecutionViewing } = useTaskReminders();
  const { t } = useI18n();
  const [execution, setExecution] = useState<TaskExecution | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [imagePreview, setImagePreview] = useState<ResolvedArtifactFile | null>(null);
  const [working, setWorking] = useState("");
  const [resolvingApproval, setResolvingApproval] = useState<{ id: string; approved: boolean } | null>(null);
  const [resolvingInteraction, setResolvingInteraction] = useState("");
  const [artifactWorking, setArtifactWorking] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [technicalExpanded, setTechnicalExpanded] = useState(false);
  const [stepsExpanded, setStepsExpanded] = useState(false);
  const [followUp, setFollowUp] = useState("");
  const [followUpFiles, setFollowUpFiles] = useState<PendingFollowUpFile[]>([]);
  const executionRef = useRef<TaskExecution | null>(null);
  executionRef.current = execution;

  const refresh = useCallback(async () => {
    if (!gateway.client || !executionId) return;
    try {
      const snapshot = await gateway.runAuthenticated((client) => client.getTaskExecution(executionId));
      const definition = await gateway.runAuthenticated((client) => client.getTask(snapshot.task_id));
      setExecution(snapshot);
      setTask(definition);
      setError("");
      void storeExecutionCache(executionId, { execution: snapshot, task: definition });
      setExecutionViewing(executionId);
    } catch {
      if (!executionRef.current) {
        setError(t("execution.loadFailed"));
      }
    }
  }, [executionId, gateway.client, gateway.runAuthenticated, setExecutionViewing, t]);

  useEffect(() => {
    let active = true;
    void loadExecutionCache(executionId).then((cached) => {
      if (!active || !cached) return;
      setExecution((current) => current ?? cached.execution);
      setTask((current) => current ?? cached.task);
      setExecutionViewing(executionId);
    });
    return () => { active = false; };
  }, [executionId, setExecutionViewing]);

  useEffect(() => {
    if (!executionId || gateway.status !== "ready") return;
    void refresh();
  }, [executionId, gateway.status, refresh]);

  useEffect(() => () => setExecutionViewing(null), [executionId, setExecutionViewing]);

  useEffect(() => {
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = gateway.subscribeEvents((feed) => {
      const event = feed.event;
      if (event.task_id !== executionId || !shouldRefreshExecution(event.event_type)) return;
      if (refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void gateway.runAuthenticated((client) => client.getTaskExecution(executionId))
          .then((snapshot) => {
            setExecution(snapshot);
            setError("");
          })
          .catch(() => undefined);
      }, 300);
    });
    return () => {
      unsubscribe();
      if (refreshTimer) clearTimeout(refreshTimer);
    };
  }, [executionId, gateway.runAuthenticated, gateway.subscribeEvents]);

  const approvals = useMemo(
    () => execution?.approvals.filter((item) => item.state === "pending") ?? [],
    [execution?.approvals],
  );
  const interactions = useMemo(
    () => execution?.interactions?.filter((item) => item.state === "pending") ?? [],
    [execution?.interactions],
  );
  const timeline = useMemo(
    () => mergeTaskTimeline((execution?.trace?.entries ?? []).filter(
      (entry) => entry.entry_type !== "final_output"
        && (entry.entry_type !== "content" || !execution?.final_result),
    ), execution?.state),
    [execution?.final_result, execution?.state, execution?.trace?.entries],
  );
  const terminal = execution ? isTerminal(execution.state) : false;
  const summaryText = execution?.launch_reason === "event"
    ? task?.title ?? t("taskDetail.reason.event")
    : execution?.goal_snapshot ?? "";

  async function command(action: "cancel" | "pause" | "resume" | "rerun") {
    if (!execution || working) return;
    setWorking(action);
    setError("");
    try {
      const next = await gateway.runAuthenticated((client) => client.taskExecutionCommand(execution.execution_id, action));
      if (action === "rerun" && next) {
        router.replace(`/task-executions/${next.execution_id}`);
        return;
      }
      await refresh();
    } catch {
      setError(t("execution.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  async function chooseFollowUpFiles() {
    const picked = await DocumentPicker.getDocumentAsync({
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (picked.canceled) return;
    setFollowUpFiles((current) => [
      ...current,
      ...picked.assets.slice(0, 8 - current.length).map((asset) => ({
        uri: asset.uri,
        name: asset.name,
        mediaType: asset.mimeType ?? "application/octet-stream",
      })),
    ].slice(0, 8));
  }

  async function submitFollowUp() {
    if (!execution || !task || working || (!followUp.trim() && !followUpFiles.length)) return;
    setWorking("follow-up");
    setError("");
    try {
      const attachments: ArtifactInput[] = [];
      for (const item of followUpFiles) {
        const response = await fetch(item.uri);
        const bytes = await response.arrayBuffer();
        const uploaded = await gateway.runAuthenticated((client) => client.uploadArtifact({
          sessionHandle: task.session_handle,
          bytes,
          mediaType: item.mediaType,
          name: item.name,
          caption: item.name,
        }));
        attachments.push(uploaded);
      }
      const next = await gateway.runAuthenticated((client) => client.continueTask({
        clientRequestId: Crypto.randomUUID(),
        taskId: execution.task_id,
        text: followUp.trim(),
        attachments,
      }));
      setFollowUp("");
      setFollowUpFiles([]);
      router.push(`/task-executions/${next.execution_id}`);
    } catch {
      setError(t("execution.followUpFailed"));
    } finally {
      setWorking("");
    }
  }

  async function openArtifact(artifact: ChatArtifact, action: "open" | "save" = "open") {
    if (artifactWorking) return;
    setArtifactWorking(`${artifact.artifact_id}:${action}`);
    setError("");
    setMessage("");
    try {
      const downloaded = await gateway.runAuthenticated((client) => client.downloadArtifact(
        gateway.sessionHandle,
        artifact.artifact_id,
      ));
      const safeName = downloaded.name.replace(/[^\p{L}\p{N}._ -]/gu, "_");
      const file = new File(Paths.cache, `${artifact.artifact_id}-${safeName}`);
      file.create({ overwrite: true, intermediates: true });
      file.write(downloaded.bytes);
      const resolved = { uri: file.uri, name: downloaded.name, mediaType: downloaded.mediaType };
      if (action === "save") {
        setMessage(await saveArtifactFile(resolved, {
          saveDialog: t("artifact.save"),
          saveToFile: t("artifact.saveToFile"),
          cancelled: t("artifact.saveCancelled"),
          saved: t("artifact.savedFile"),
        }));
      } else if (downloaded.mediaType.startsWith("image/")) {
        setImagePreview(resolved);
      } else {
        await Sharing.shareAsync(file.uri, { mimeType: downloaded.mediaType });
      }
    } catch {
      setError(action === "save" ? t("execution.saveFailed") : t("execution.openFailed"));
    } finally {
      setArtifactWorking("");
    }
  }

  function confirmDelete() {
    if (!execution || !isTerminal(execution.state)) return;
    Alert.alert(t("execution.deleteTitle"), t("execution.deleteBody"), [
      { text: t("common.cancel"), style: "cancel" },
      { text: t("common.delete"), style: "destructive", onPress: () => void deleteExecution() },
    ]);
  }

  async function deleteExecution() {
    if (!execution) return;
    setWorking("delete");
    try {
      await gateway.runAuthenticated((client) => client.deleteTaskExecution(execution.execution_id));
      router.replace(`/tasks/${execution.task_id}`);
    } catch {
      setError(t("execution.deleteFailed"));
      setWorking("");
    }
  }

  if (!execution && !error && gateway.status === "ready") return <AsyncStateView state="loading" />;
  if (!execution) return (
    <AsyncStateView state="error" message={error || t("chat.reconnecting")} retryLabel={t("tasks.reload")} onRetry={() => void refresh()} />
  );

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.summary}>
        <View style={styles.summaryHeader}>
          <Text style={styles.reason}>{launchReasonLabel(execution.launch_reason, t)}</Text>
          <Text style={styles.state}>{execution.work_status ? userWorkStatusLabel(execution.work_status.status, t) : stateLabel(execution.state, t)}</Text>
        </View>
        <Text style={styles.goal}>{summaryText}</Text>
        <Text style={styles.snapshot}>{formatExecutionTime(execution.created_at, t("execution.started"))}</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {message ? <Text style={styles.message}>{message}</Text> : null}

      {approvals.map((approval, index) => (
        <View key={approval.approval_id} style={styles.approval}>
          {approvals.length > 1 ? <Text style={styles.approvalCount}>{index + 1}/{approvals.length}</Text> : null}
          <ApprovalRequestDetails toolName={approval.tool_name} arguments={approval.arguments} display={approval.display} />
          <View style={styles.row}>
            <Action
              label={t("execution.denyAction")}
              onPress={() => void resolveApproval(approval, false)}
              disabled={Boolean(working || resolvingApproval)}
              busy={resolvingApproval?.id === approval.approval_id && resolvingApproval.approved === false}
            />
            <Action
              label={t("execution.allowAction")}
              primary
              onPress={() => void resolveApproval(approval, true)}
              disabled={Boolean(working || resolvingApproval)}
              busy={resolvingApproval?.id === approval.approval_id && resolvingApproval.approved === true}
            />
          </View>
        </View>
      ))}

      {interactions.map((interaction) => (
        <InteractionCard
          key={interaction.interaction_id}
          interaction={interaction}
          submitting={resolvingInteraction === interaction.interaction_id}
          onSubmit={(value) => void resolveInteraction(interaction, value)}
        />
      ))}

      <WorkResultSummary execution={execution} />

      {timeline.length ? (
        <View style={styles.timeline}>
          {terminal ? (
            <AppPressable
              onPress={() => setStepsExpanded((value) => !value)}
              style={styles.stepsToggle}
            >
              <Text style={styles.sectionTitle}>
                {stepsExpanded ? t("execution.hideSteps") : t("execution.showSteps")}
              </Text>
              <AppIcon name={stepsExpanded ? "chevron-down" : "chevron-right"} color={colors.muted} size={17} />
            </AppPressable>
          ) : <Text style={styles.sectionTitle}>{t("execution.steps")}</Text>}
          {!terminal || stepsExpanded ? timeline.map((entry) => (
            <TraceEntry
              key={entry.key}
              entry={entry}
              artifactWorking={artifactWorking}
              onArtifact={(artifact, action) => void openArtifact(artifact, action)}
              t={t}
            />
          )) : null}
        </View>
      ) : null}

      <View style={styles.row}>
        {execution.state === "running" || execution.state === "waiting_approval" || execution.state === "queued" ? (
          <>
            <Action label={t("execution.pause")} onPress={() => void command("pause")} disabled={Boolean(working)} busy={working === "pause"} />
            <Action label={t("execution.stop")} danger onPress={() => void command("cancel")} disabled={Boolean(working)} busy={working === "cancel"} />
          </>
        ) : null}
        {execution.state === "paused" ? <Action label={t("execution.resume")} primary onPress={() => void command("resume")} disabled={Boolean(working)} busy={working === "resume"} /> : null}
        {isTerminal(execution.state) ? <Action label={t("execution.rerun")} primary onPress={() => void command("rerun")} disabled={Boolean(working)} busy={working === "rerun"} /> : null}
      </View>

      {isTerminal(execution.state) ? (
        <View style={styles.followUpCard}>
          <Text style={styles.sectionTitle}>{t("execution.followUpTitle")}</Text>
          <Text style={styles.followUpHint}>{t("execution.followUpHint")}</Text>
          <TextInput
            multiline
            value={followUp}
            onChangeText={setFollowUp}
            placeholder={t("execution.followUpPlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.followUpInput}
          />
          {followUpFiles.map((file, index) => (
            <View key={`${file.uri}:${index}`} style={styles.followUpFile}>
              <Text numberOfLines={1} style={styles.followUpFileName}>{file.name}</Text>
              <AppPressable onPress={() => setFollowUpFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>
                <Text style={styles.followUpRemove}>{t("common.delete")}</Text>
              </AppPressable>
            </View>
          ))}
          <View style={styles.row}>
            <Action label={t("execution.addEvidence")} onPress={() => void chooseFollowUpFiles()} disabled={Boolean(working) || followUpFiles.length >= 8} />
            <Action label={t("execution.continueAnalysis")} primary onPress={() => void submitFollowUp()} disabled={Boolean(working) || (!followUp.trim() && !followUpFiles.length)} busy={working === "follow-up"} />
          </View>
        </View>
      ) : null}

      <AppPressable onPress={() => setTechnicalExpanded((value) => !value)} style={styles.technicalToggle}>
        <Text style={styles.technicalToggleText}>{technicalExpanded ? t("execution.hideTechnical") : t("execution.showTechnical")}</Text>
        <AppIcon name="chevron-right" color={colors.muted} size={17} />
      </AppPressable>
      {technicalExpanded ? (
        <View style={styles.technicalCard}>
          <Text style={styles.technicalLine}>{t("agent.executionSnapshot", { agent: agentName(execution.agent_id_snapshot, gateway.agents) })}</Text>
          <Text style={styles.technicalLine}>{t("execution.taskRevision", { revision: execution.task_revision })}</Text>
          {execution.phase ? <Text selectable style={styles.technicalLine}>{t("execution.phase", { phase: execution.phase })}</Text> : null}
          {execution.failure_code ? <Text selectable style={styles.technicalLine}>{t("execution.failureCode", { code: execution.failure_code })}</Text> : null}
          {execution.launch_reason === "event" ? (
            <>
              <Text style={styles.technicalLabel}>{t("execution.eventInput")}</Text>
              <Text selectable style={styles.technicalPayload}>{execution.goal_snapshot}</Text>
            </>
          ) : null}
        </View>
      ) : null}

      {isTerminal(execution.state) ? (
        <AppPressable disabled={Boolean(working)} onPress={confirmDelete} style={styles.deleteButton}>
          {working === "delete"
            ? <ActivityIndicator color={colors.danger} size="small" />
            : <Text style={styles.deleteText}>{t("execution.deleteRecord")}</Text>}
        </AppPressable>
      ) : null}
      <ArtifactViewer
        file={imagePreview}
        onClose={() => setImagePreview(null)}
        onMessage={(value, tone) => {
          if (tone === "error") setError(value);
          else setMessage(value);
        }}
      />
    </ScrollView>
  );

  async function resolveApproval(approval: TaskApproval, approved: boolean) {
    if (working || resolvingApproval) return;
    setResolvingApproval({ id: approval.approval_id, approved });
    try {
      await gateway.runAuthenticated((client) => client.resolveApproval(approval.approval_id, approved));
      setExecution((current) => current ? {
        ...current,
        approvals: current.approvals.map((item) => item.approval_id === approval.approval_id
          ? { ...item, state: approved ? "approved" : "denied" }
          : item),
      } : current);
      void refresh();
    } catch {
      setError(t("execution.approvalFailed"));
    } finally {
      setResolvingApproval(null);
    }
  }

  async function resolveInteraction(interaction: HumanInteraction, value: Record<string, unknown>) {
    if (working || resolvingInteraction) return;
    setResolvingInteraction(interaction.interaction_id);
    try {
      const result = await gateway.runAuthenticated(
        (client) => client.resolveInteraction(interaction.interaction_id, value),
      );
      setExecution((current) => current ? {
        ...current,
        interactions: (current.interactions ?? []).map((item) => item.interaction_id === interaction.interaction_id ? result.interaction : item),
      } : current);
      void refresh();
    } catch {
      setError(t("interaction.submitFailed"));
    } finally {
      setResolvingInteraction("");
    }
  }
}

function isTerminal(state: TaskExecution["state"]): boolean {
  return state === "completed" || state === "failed" || state === "cancelled";
}

function launchReasonLabel(reason: TaskExecution["launch_reason"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({ created: t("taskDetail.reason.created"), manual: t("taskDetail.reason.manual"), scheduled: t("taskDetail.reason.scheduled"), event: t("taskDetail.reason.event"), rerun: t("taskDetail.reason.rerun"), follow_up: t("taskDetail.reason.followUp") })[reason];
}

function stateLabel(state: TaskExecution["state"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({ queued: t("taskState.queued"), running: t("taskState.running"), waiting_approval: t("taskState.waitingApproval"), paused: t("tasks.state.paused"), completed: t("taskState.completed"), failed: t("taskState.failed"), cancelled: t("taskState.cancelled") })[state];
}

function userWorkStatusLabel(status: NonNullable<TaskExecution["work_status"]>["status"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({ queued: t("taskState.queued"), working: t("taskState.running"), waiting_for_you: t("taskState.waitingApproval"), completed: t("taskState.completed"), failed: t("taskState.failed"), paused: t("tasks.state.paused"), cancelled: t("taskState.cancelled") })[status];
}

function TraceEntry({
  entry,
  artifactWorking,
  onArtifact,
  t,
}: {
  entry: TaskTimelineItem;
  artifactWorking: string;
  onArtifact(artifact: ChatArtifact, action: "open" | "save"): void;
  t: ReturnType<typeof useI18n>["t"];
}) {
  if (entry.kind === "tool") {
    if (entry.state === "running") return (
      <View style={styles.toolRow}>
        <ActivityIndicator color={colors.accent} size="small" />
        <Text style={styles.toolName}>{entry.toolName || t("execution.tool")}</Text>
        <Text style={styles.toolState}>{t("execution.running")}</Text>
      </View>
    );
    const presentation = {
      completed: { symbol: "✓", label: t("execution.completed"), color: colors.accent },
      failed: { symbol: "!", label: t("taskState.failed"), color: colors.danger },
      cancelled: { symbol: "×", label: t("taskState.cancelled"), color: colors.muted },
      incomplete: { symbol: "!", label: t("execution.notCompleted"), color: colors.warning },
    }[entry.state];
    return (
      <View style={styles.toolRow}>
        <Text style={[styles.toolResult, { color: presentation.color }]}>{presentation.symbol}</Text>
        <Text style={styles.toolName}>{entry.toolName || t("execution.tool")}</Text>
        <Text style={styles.toolState}>{presentation.label}</Text>
      </View>
    );
  }
  const source = entry.entry;
  if (source.entry_type === "reasoning") return source.content ? <Text style={styles.reasoning}>{source.content}</Text> : null;
  if (source.entry_type === "warning") return <Text style={styles.warning}>{source.content}</Text>;
  if (source.entry_type === "artifact") {
    const artifact = source.artifact;
    if (!artifact) return null;
    const opening = artifactWorking === `${artifact.artifact_id}:open`;
    const saving = artifactWorking === `${artifact.artifact_id}:save`;
    return (
      <View style={styles.artifactRow}>
        <Text style={styles.artifact}>{t("execution.attachment")}</Text>
        <AppPressable disabled={Boolean(artifactWorking)} onPress={() => onArtifact(artifact, "open")} style={styles.artifactAction}>
          {opening ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.artifactActionText}>{t("execution.open")}</Text>}
        </AppPressable>
        <AppPressable disabled={Boolean(artifactWorking)} onPress={() => onArtifact(artifact, "save")} style={styles.artifactAction}>
          {saving ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.artifactActionText}>{t("execution.save")}</Text>}
        </AppPressable>
      </View>
    );
  }
  if (source.entry_type === "content" || source.entry_type === "plan") return source.content ? <AppMarkdown value={source.content} style={styles.markdown} /> : null;
  return null;
}

function formatExecutionTime(createdAt: number, label: string): string {
  return `${label} · ${new Date(createdAt * 1000).toLocaleString()}`;
}

function agentName(agentId: string, agents: AgentSummary[]): string {
  return agents.find((agent) => agent.agent_id === agentId)?.display_name ?? agentId;
}

type PendingFollowUpFile = {
  uri: string;
  name: string;
  mediaType: string;
};

function Action({ label, primary = false, danger = false, disabled = false, busy = false, onPress }: { label: string; primary?: boolean; danger?: boolean; disabled?: boolean; busy?: boolean; onPress(): void }) {
  return (
    <AppPressable disabled={disabled} style={[styles.action, primary && styles.actionPrimary, danger && styles.actionDanger, disabled && styles.disabled]} onPress={onPress}>
      {busy
        ? <ActivityIndicator color={primary ? colors.onAccent : colors.accent} size="small" />
        : <Text style={[styles.actionText, primary && styles.actionPrimaryText, danger && styles.actionDangerText]}>{label}</Text>}
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.large, paddingBottom: 48 },
  summary: { backgroundColor: colors.surface, borderRadius: radii.large, padding: spacing.xlarge, borderWidth: 1, borderColor: colors.line, gap: spacing.small , ...shadows.card },
  summaryHeader: { flexDirection: "row", justifyContent: "space-between" },
  reason: { color: colors.ink, fontWeight: "700" },
  state: { color: colors.accent, fontWeight: "700" },
  goal: { color: colors.ink, fontSize: 18, lineHeight: 27, fontWeight: "600" },
  phase: { color: colors.muted },
  snapshot: { color: colors.muted, fontSize: 12 },
  final: { backgroundColor: colors.surface, borderRadius: radii.large, padding: spacing.xlarge, borderWidth: 1, borderColor: colors.line, gap: spacing.small , ...shadows.card },
  markdown: { width: "100%", alignSelf: "stretch" },
  failure: { padding: spacing.xlarge, borderRadius: radii.large, backgroundColor: colors.dangerSoft, gap: spacing.small },
  failureTitle: { color: colors.danger, fontWeight: "700" },
  failureText: { color: colors.ink },
  failureImpact: { color: colors.warning, lineHeight: 21 },
  approval: { padding: spacing.xlarge, borderRadius: radii.large, backgroundColor: colors.warningSoft, borderWidth: 1, borderColor: colors.warning, gap: spacing.small },
  approvalCount: { color: colors.muted, fontSize: 12, textAlign: "right" },
  row: { flexDirection: "row", gap: spacing.medium },
  timeline: { backgroundColor: colors.surface, borderRadius: radii.large, padding: spacing.xlarge, borderWidth: 1, borderColor: colors.line, gap: spacing.medium , ...shadows.card },
  stepsToggle: { minHeight: 30, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  followUpCard: { backgroundColor: colors.surface, borderRadius: radii.large, padding: spacing.xlarge, borderWidth: 1, borderColor: colors.line, gap: spacing.medium , ...shadows.card },
  followUpHint: { color: colors.muted, lineHeight: 20 },
  followUpInput: { minHeight: 96, borderWidth: 1, borderColor: colors.line, borderRadius: radii.medium, padding: spacing.medium, color: colors.ink, textAlignVertical: "top", backgroundColor: colors.surfaceMuted },
  followUpFile: { minHeight: 34, flexDirection: "row", alignItems: "center", gap: spacing.small },
  followUpFileName: { color: colors.ink, flex: 1, fontSize: 13 },
  followUpRemove: { color: colors.danger, ...typography.small },
  sectionTitle: { color: colors.ink, fontWeight: "700", fontSize: 17, marginBottom: spacing.xsmall },
  reasoning: { color: colors.muted, lineHeight: 22 },
  toolRow: { minHeight: 30, flexDirection: "row", alignItems: "center", gap: spacing.small },
  toolName: { color: colors.ink, flex: 1 },
  toolState: { color: colors.muted, fontSize: 12 },
  toolResult: { color: colors.accent, width: 18, textAlign: "center", fontWeight: "800" },
  warning: { color: colors.warning },
  artifact: { color: colors.accent, fontWeight: "600" },
  artifactRow: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: spacing.small },
  artifactAction: { minWidth: 54, minHeight: 36, borderRadius: radii.small, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" },
  artifactActionText: { color: colors.accent, fontWeight: "700", fontSize: 12 },
  action: { flex: 1, minHeight: 46, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft, borderRadius: radii.medium, paddingHorizontal: spacing.medium },
  actionText: { color: colors.accent, fontWeight: "700", textAlign: "center" },
  actionPrimary: { backgroundColor: colors.accent },
  actionPrimaryText: { color: colors.onAccent },
  actionDanger: { backgroundColor: colors.dangerSoft },
  actionDangerText: { color: colors.danger },
  disabled: { opacity: 0.45 },
  error: { color: colors.danger, lineHeight: 21 },
  message: { color: colors.accent, lineHeight: 21 },
  technicalToggle: { minHeight: 44, paddingHorizontal: spacing.xsmall, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.small },
  technicalToggleText: { color: colors.muted, fontWeight: "600" },
  technicalCard: { padding: spacing.large, borderRadius: radii.medium, backgroundColor: colors.surfaceMuted, gap: spacing.small },
  technicalLabel: { color: colors.ink, ...typography.small, fontWeight: "700", marginTop: spacing.small },
  technicalLine: { color: colors.muted, fontSize: 12, fontFamily: "monospace" },
  technicalPayload: { color: colors.muted, fontSize: 11, fontFamily: "monospace", lineHeight: 16 },
  deleteButton: { alignItems: "center", padding: spacing.large, marginTop: spacing.small },
  deleteText: { color: colors.danger, fontWeight: "600" },
});
