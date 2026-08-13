import * as DocumentPicker from "expo-document-picker";
import * as Crypto from "expo-crypto";
import { router, useLocalSearchParams } from "expo-router";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useState } from "react";
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
import { AppPressable } from "@/components/AppPressable";
import { AppIcon } from "@/components/AppIcon";
import { AppMarkdown } from "@/components/AppMarkdown";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { InteractionCard } from "@/components/InteractionCard";
import { mergeTaskTimeline, type TaskTimelineItem } from "@/components/taskTimeline";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function TaskExecutionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const executionId = String(id ?? "");
  const gateway = useGateway();
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
  const [followUp, setFollowUp] = useState("");
  const [followUpFiles, setFollowUpFiles] = useState<PendingFollowUpFile[]>([]);

  const refresh = useCallback(async () => {
    if (!gateway.client || !executionId) return;
    setError("");
    try {
      const snapshot = await gateway.runAuthenticated((client) => client.getTaskExecution(executionId));
      const definition = await gateway.runAuthenticated((client) => client.getTask(snapshot.task_id));
      setExecution(snapshot);
      setTask(definition);
    } catch {
      setError(t("execution.loadFailed"));
    }
  }, [executionId, gateway.client, gateway.runAuthenticated, t]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const event = gateway.latestEvent?.event;
    if (!event || event.task_id !== executionId) return;
    void gateway.runAuthenticated((client) => client.getTaskExecution(executionId)).then(setExecution);
  }, [executionId, gateway.latestEvent, gateway.runAuthenticated]);

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
    )),
    [execution?.final_result, execution?.trace?.entries],
  );

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

  if (!execution && !error) return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;
  if (!execution) return (
    <View style={styles.loading}>
      <Text style={styles.error}>{error}</Text>
      <AppPressable onPress={() => void refresh()}><Text style={styles.link}>{t("tasks.reload")}</Text></AppPressable>
    </View>
  );

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.summary}>
        <View style={styles.summaryHeader}>
          <Text style={styles.reason}>{launchReasonLabel(execution.launch_reason, t)}</Text>
          <Text style={styles.state}>{stateLabel(execution.state, t)}</Text>
        </View>
        <Text style={styles.goal}>{execution.goal_snapshot}</Text>
        <Text style={styles.snapshot}>{formatExecutionTime(execution.created_at, t("execution.started"))}</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {message ? <Text style={styles.message}>{message}</Text> : null}

      {execution.final_result ? (
        <View style={styles.final}><AppMarkdown value={execution.final_result} style={styles.markdown} /></View>
      ) : null}
      {execution.failure_code ? (
        <View style={styles.failure}><Text style={styles.failureTitle}>{t("execution.incomplete")}</Text><Text style={styles.failureText}>{t("execution.incompleteHelp")}</Text></View>
      ) : null}

      {approvals.map((approval, index) => (
        <View key={approval.approval_id} style={styles.approval}>
          <Text style={styles.approvalTitle}>{t("execution.approvalTitle")}{approvals.length > 1 ? ` · ${index + 1}/${approvals.length}` : ""}</Text>
          <ApprovalDetails approval={approval} t={t} />
          <View style={styles.row}>
            <Action
              label={t("common.cancel")}
              onPress={() => void resolveApproval(approval, false)}
              disabled={Boolean(working || resolvingApproval)}
              busy={resolvingApproval?.id === approval.approval_id && resolvingApproval.approved === false}
            />
            <Action
              label={t("execution.confirm")}
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

      {timeline.length ? (
        <View style={styles.timeline}>
          <Text style={styles.sectionTitle}>{t("execution.steps")}</Text>
          {timeline.map((entry) => (
            <TraceEntry
              key={entry.key}
              entry={entry}
              artifactWorking={artifactWorking}
              onArtifact={(artifact, action) => void openArtifact(artifact, action)}
              t={t}
            />
          ))}
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
      setError("输入未能提交，请刷新后重试");
    } finally {
      setResolvingInteraction("");
    }
  }
}

function ApprovalDetails({ approval, t }: { approval: TaskApproval; t: ReturnType<typeof useI18n>["t"] }) {
  const [effect, risk] = approval.reason.split(":", 2);
  const hasArguments = Object.keys(approval.arguments).length > 0;
  return (
    <View style={styles.approvalDetails}>
      <Text style={styles.approvalLabel}>{t("execution.tool")}</Text>
      <Text selectable style={styles.tool}>{approval.tool_name}</Text>
      <Text style={styles.approvalReason}>{t("execution.effect", { value: effectLabel(effect ?? "", t) })}</Text>
      <Text style={styles.approvalReason}>{t("execution.risk", { value: riskLabel(risk ?? "", t) })}</Text>
      <Text style={styles.approvalReason}>{t("execution.reversibility")}</Text>
      {hasArguments ? (
        <>
          <Text style={styles.approvalLabel}>{t("execution.arguments")}</Text>
          <Text selectable style={styles.arguments}>{JSON.stringify(approval.arguments, null, 2)}</Text>
        </>
      ) : <Text style={styles.approvalReason}>{t("execution.noArguments")}</Text>}
    </View>
  );
}

function effectLabel(value: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ local_write: t("execution.effect.local"), external_side_effect: t("execution.effect.external"), desktop_control: t("execution.effect.desktop"), unknown: t("execution.effect.unknown") } as Record<string, string>)[value] ?? t("execution.effect.controlled");
}

function riskLabel(value: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ low: t("execution.risk.low"), medium: t("execution.risk.medium"), high: t("execution.risk.high") } as Record<string, string>)[value] ?? t("execution.risk.unknown");
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
  if (entry.kind === "tool") return (
    <View style={styles.toolRow}>
      {entry.state === "running" ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.toolResult}>✓</Text>}
      <Text style={styles.toolName}>{entry.toolName || t("execution.tool")}</Text>
      <Text style={styles.toolState}>{entry.state === "running" ? t("execution.running") : t("execution.completed")}</Text>
    </View>
  );
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
        ? <ActivityIndicator color={primary ? "white" : colors.accent} size="small" />
        : <Text style={[styles.actionText, primary && styles.actionPrimaryText, danger && styles.actionDangerText]}>{label}</Text>}
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 24 },
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  summary: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 8 },
  summaryHeader: { flexDirection: "row", justifyContent: "space-between" },
  reason: { color: colors.ink, fontWeight: "700" },
  state: { color: colors.accent, fontWeight: "700" },
  goal: { color: colors.ink, fontSize: 18, lineHeight: 27, fontWeight: "600" },
  phase: { color: colors.muted },
  snapshot: { color: colors.muted, fontSize: 12 },
  final: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line },
  markdown: { width: "100%", alignSelf: "stretch" },
  failure: { padding: 18, borderRadius: 18, backgroundColor: colors.dangerSoft, gap: 6 },
  failureTitle: { color: colors.danger, fontWeight: "700" },
  failureText: { color: colors.ink },
  approval: { padding: 18, borderRadius: 18, backgroundColor: colors.warningSoft, borderWidth: 1, borderColor: colors.warning, gap: 8 },
  approvalTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  tool: { color: colors.accent, fontFamily: "monospace", fontSize: 13 },
  approvalLabel: { color: colors.ink, fontWeight: "700", marginTop: 3 },
  approvalReason: { color: colors.ink, lineHeight: 22 },
  approvalDetails: { gap: 5 },
  arguments: { color: colors.ink, fontFamily: "monospace", fontSize: 12, backgroundColor: colors.surface, borderRadius: 10, padding: 10 },
  row: { flexDirection: "row", gap: 10 },
  timeline: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 10 },
  followUpCard: { backgroundColor: colors.surface, borderRadius: 18, padding: 18, borderWidth: 1, borderColor: colors.line, gap: 10 },
  followUpHint: { color: colors.muted, lineHeight: 20 },
  followUpInput: { minHeight: 96, borderWidth: 1, borderColor: colors.line, borderRadius: 13, padding: 12, color: colors.ink, textAlignVertical: "top", backgroundColor: colors.surfaceMuted },
  followUpFile: { minHeight: 34, flexDirection: "row", alignItems: "center", gap: 8 },
  followUpFileName: { color: colors.ink, flex: 1, fontSize: 13 },
  followUpRemove: { color: colors.danger, fontSize: 12, fontWeight: "600" },
  sectionTitle: { color: colors.ink, fontWeight: "700", fontSize: 17, marginBottom: 4 },
  reasoning: { color: colors.muted, lineHeight: 22 },
  toolRow: { minHeight: 30, flexDirection: "row", alignItems: "center", gap: 8 },
  toolName: { color: colors.ink, flex: 1 },
  toolState: { color: colors.muted, fontSize: 12 },
  toolResult: { color: colors.accent, width: 18, textAlign: "center", fontWeight: "800" },
  warning: { color: colors.warning },
  artifact: { color: colors.accent, fontWeight: "600" },
  artifactRow: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: 8 },
  artifactAction: { minWidth: 54, minHeight: 36, borderRadius: 9, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" },
  artifactActionText: { color: colors.accent, fontWeight: "700", fontSize: 12 },
  action: { flex: 1, minHeight: 46, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft, borderRadius: 13, paddingHorizontal: 10 },
  actionText: { color: colors.accent, fontWeight: "700", textAlign: "center" },
  actionPrimary: { backgroundColor: colors.accent },
  actionPrimaryText: { color: "white" },
  actionDanger: { backgroundColor: colors.dangerSoft },
  actionDangerText: { color: colors.danger },
  disabled: { opacity: 0.45 },
  error: { color: colors.danger, lineHeight: 21 },
  message: { color: colors.accent, lineHeight: 21 },
  link: { color: colors.accent, fontWeight: "700" },
  technicalToggle: { minHeight: 44, paddingHorizontal: 4, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  technicalToggleText: { color: colors.muted, fontWeight: "600" },
  technicalCard: { padding: 14, borderRadius: 14, backgroundColor: colors.surfaceMuted, gap: 6 },
  technicalLine: { color: colors.muted, fontSize: 12, fontFamily: "monospace" },
  deleteButton: { alignItems: "center", padding: 14, marginTop: 8 },
  deleteText: { color: colors.danger, fontWeight: "600" },
});
