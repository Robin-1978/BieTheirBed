import { router, useLocalSearchParams } from "expo-router";
import * as Crypto from "expo-crypto";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Switch,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { immediatePolicy, isLaunchPolicyValid, TaskLaunchEditor } from "@/components/TaskLaunchEditor";
import { AgentSelector } from "@/components/AgentSelector";
import type { MCPResourceCatalogItem, TaskLaunchPolicy } from "@/api/models";
import { useI18n } from "@/i18n";
import { AppPressable } from "@/components/AppPressable";
import { AppIcon } from "@/components/AppIcon";
import { TASK_TEMPLATES } from "@/taskTemplates";
import { enqueueOfflineTask } from "@/storage/offlineTaskQueue";
import { requestTaskNotificationPermission } from "@/notifications/taskNotifications";
import { presentNodeName } from "@/presentation/nodePresentation";
import { MAX_ATTACHMENTS, pickAttachments, type PickedAttachment } from "@/media/attachmentPicker";
import { uploadSessionAttachments } from "@/api/uploadAttachments";
import { pickFolderSnapshot, uploadFolderSnapshot, type FolderSelection } from "@/media/folderManifest";

export default function NewTaskScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ template?: string; workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notifyCompleted, setNotifyCompleted] = useState(true);
  const [notifyFailed, setNotifyFailed] = useState(true);
  const [notifyApproval, setNotifyApproval] = useState(true);
  const [launchPolicy, setLaunchPolicy] = useState<TaskLaunchPolicy>(immediatePolicy);
  const [agentId, setAgentId] = useState(gateway.defaultAgentId || "knoa");
  const [mcpResources, setMcpResources] = useState<MCPResourceCatalogItem[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState(gateway.nodeId || params.nodeId || "");
  const [switchingNode, setSwitchingNode] = useState(false);
  const [attachments, setAttachments] = useState<PickedAttachment[]>([]);
  const [folder, setFolder] = useState<FolderSelection | null>(null);
  const [folderProgress, setFolderProgress] = useState(0);
  const requestIdentity = useRef<{ fingerprint: string; requestId: string } | null>(null);

  async function chooseAttachments() {
    try {
      const prepared = await pickAttachments(attachments.length);
      if (prepared.length) setAttachments((current) => [...current, ...prepared].slice(0, MAX_ATTACHMENTS));
    } catch {
      setError(t("taskNew.attachmentPickFailed"));
    }
  }

  async function chooseFolder() {
    try {
      setFolder(await pickFolderSnapshot());
      setFolderProgress(0);
    } catch (caught) {
      if (caught instanceof RangeError) setError(folderErrorMessage(caught.message, t));
      else setError(t("taskNew.folderPickFailed"));
    }
  }

  useEffect(() => {
    if (!gateway.client) return;
    void gateway.runAuthenticated((client) => client.listMcpResources())
      .then(setMcpResources)
      .catch(() => setMcpResources([]));
  }, [gateway.client, gateway.runAuthenticated]);

  const activeTemplate = TASK_TEMPLATES.find((template) => template.id === selectedTemplate);

  useEffect(() => {
    const requested = TASK_TEMPLATES.find((template) => template.id === params.template);
    if (!requested) return;
    setSelectedTemplate(requested.id);
    setTitle(t(requested.titleKey));
    setGoal(t(requested.goalKey));
  }, [params.template, t]);

  useEffect(() => {
    if (gateway.nodeId) setSelectedNodeId(gateway.nodeId);
  }, [gateway.nodeId]);

  async function chooseNode(nodeId: string) {
    if (!nodeId || nodeId === gateway.nodeId || switchingNode) return;
    setSwitchingNode(true);
    setError("");
    try {
      await gateway.switchNode(nodeId);
      setSelectedNodeId(nodeId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("taskNew.nodeSwitchFailed"));
    } finally {
      setSwitchingNode(false);
    }
  }

  async function create() {
    if (gateway.requiredUpdate) {
      router.replace("/update");
      return;
    }
    const normalizedGoal = goal.trim();
    if (!normalizedGoal || saving) return;
    setSaving(true);
    setError("");
    try {
      if (notifyCompleted || notifyFailed || notifyApproval) {
        void requestTaskNotificationPermission();
      }
      let uploadedAttachments: Awaited<ReturnType<typeof uploadSessionAttachments>>["uploaded"] = [];
      if (attachments.length) {
        // Attachments live in a conversation session; tasks reference the
        // uploaded artifact ids. Offline queues cannot carry file bytes yet.
        const sessionHandle = await gateway.ensureConversation();
        const result = await gateway.runAuthenticated(
          (client) => uploadSessionAttachments(client, sessionHandle, attachments),
        );
        if (result.failed) {
          setError(t("taskNew.attachmentUploadFailed"));
          return;
        }
        uploadedAttachments = result.uploaded;
      }
      if (folder) {
        const sessionHandle = await gateway.ensureConversation();
        const manifest = await gateway.runAuthenticated(
          (client) => uploadFolderSnapshot(
            client, sessionHandle, folder,
            (completed) => setFolderProgress(completed),
          ),
        );
        uploadedAttachments = [...uploadedAttachments, manifest];
      }
      const input = {
        title: title.trim(),
        goal: normalizedGoal,
        attachments: uploadedAttachments,
        notificationPolicy: {
          completed: notifyCompleted,
          failed: notifyFailed,
          waiting_approval: notifyApproval,
        },
        launchPolicy,
        agentId,
      };
      const fingerprint = JSON.stringify(input);
      if (requestIdentity.current?.fingerprint !== fingerprint) {
        requestIdentity.current = {
          fingerprint,
          requestId: Crypto.randomUUID(),
        };
      }
      const result = await gateway.runAuthenticated((client) => client.createTask({
        ...input,
        clientRequestId: requestIdentity.current!.requestId,
      }));
      router.replace(`/tasks/${result.task.task_id}`);
    } catch (caught) {
      // A disconnected Node must not make the user retype a long task.  Keep
      // the exact idempotency key so reconnect/retry cannot create duplicates.
      if (gateway.status !== "ready") {
        if (attachments.length || folder) {
          setError(t("taskNew.attachmentOffline"));
          return;
        }
        await enqueueOfflineTask({
          title: title.trim(),
          goal: normalizedGoal,
          notificationPolicy: {
            completed: notifyCompleted,
            failed: notifyFailed,
            waiting_approval: notifyApproval,
          },
          launchPolicy: launchPolicy as unknown as Record<string, unknown>,
          agentId,
          clientRequestId: requestIdentity.current!.requestId,
        });
        setError(t("taskNew.queuedOffline"));
      } else {
        // The server preflights create-and-run; its message (e.g. blocked
        // preflight details) is already user-facing.
        setError(caught instanceof Error && caught.message ? caught.message : t("taskNew.createFailed"));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        {gateway.requiredUpdate ? (
          <AppPressable style={styles.updateRequired} onPress={() => router.replace("/update")}>
            <Text style={styles.updateRequiredTitle}>{t("taskNew.updateRequired")}</Text>
            <Text style={styles.launchText}>{t("taskNew.updateAction")}</Text>
          </AppPressable>
        ) : null}
        <View style={styles.card}>
          <View style={styles.nodeHeader}>
            <View style={styles.flex}>
              <Text style={styles.label}>{t("taskNew.executionNode")}</Text>
              <Text style={styles.templateMeta}>{t("taskNew.executionNodeDetail")}</Text>
            </View>
            {switchingNode ? <ActivityIndicator color={colors.accent} size="small" /> : null}
          </View>
          {gateway.nodes.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.nodeRow}>
              {gateway.nodes.map((node) => (
                <AppPressable
                  key={node.nodeId}
                  style={[styles.nodeChoice, selectedNodeId === node.nodeId && styles.nodeChoiceSelected]}
                  onPress={() => void chooseNode(node.nodeId)}
                  disabled={switchingNode}
                >
                  <AppIcon name="node" color={selectedNodeId === node.nodeId ? colors.accent : colors.muted} size={17} />
                  <Text style={[styles.nodeChoiceText, selectedNodeId === node.nodeId && styles.nodeChoiceTextSelected]} numberOfLines={1}>{presentNodeName(node, t("common.unnamedComputer"))}</Text>
                  <Text style={styles.nodeChoiceStatus}>{node.nodeId === gateway.nodeId && gateway.status === "ready" ? t("taskNew.nodeReady") : t("taskNew.nodeAvailable")}</Text>
                </AppPressable>
              ))}
            </ScrollView>
          ) : <Text style={styles.templateMeta}>{t("taskNew.noNode")}</Text>}
          <Text style={styles.label}>{t("taskTemplates.title")}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.templateRow}>
            {TASK_TEMPLATES.map((template) => (
              <AppPressable
                key={template.id}
                style={[styles.template, selectedTemplate === template.id && styles.templateSelected]}
                onPress={() => {
                  setSelectedTemplate(template.id);
                  setTitle(t(template.titleKey));
                  setGoal(t(template.goalKey));
                }}
              >
                <Text style={[styles.templateTitle, selectedTemplate === template.id && styles.templateSelectedText]}>{t(template.titleKey)}</Text>
                <Text style={styles.templateDetail}>{t(template.detailKey)}</Text>
              </AppPressable>
            ))}
          </ScrollView>
          {activeTemplate ? (
            <View style={styles.templateDetails}>
              <Text style={styles.templateDetailsTitle}>{t(activeTemplate.titleKey)}</Text>
              <Text style={styles.templateMeta}>{t("taskTemplates.connection", { value: t(activeTemplate.connectionKey) })}</Text>
              <Text style={styles.templateMeta}>{t("taskTemplates.permission", { value: t(activeTemplate.permissionKey) })}</Text>
              <Text style={styles.templateMeta}>{t("taskTemplates.duration", { value: t(activeTemplate.durationKey) })}</Text>
              <Text style={styles.templateMeta}>{t("taskTemplates.result", { value: t(activeTemplate.resultKey) })}</Text>
              <Text style={styles.templateMeta}>{t("taskTemplates.failure", { value: t(activeTemplate.failureKey) })}</Text>
              <Text style={styles.templateMeta}>{t("taskTemplates.notification", { value: t(activeTemplate.notificationKey) })}</Text>
            </View>
          ) : null}
          <Text style={styles.label}>{t("taskNew.name")}</Text>
          <TextInput
            accessibilityLabel={t("taskNew.name")}
            value={title}
            onChangeText={setTitle}
            placeholder={t("taskNew.namePlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.titleInput}
            returnKeyType="next"
          />
          <Text style={styles.label}>{t("taskNew.goal")}</Text>
          <TextInput
            accessibilityLabel={t("taskNew.goal")}
            value={goal}
            onChangeText={setGoal}
            placeholder={t("taskNew.goalPlaceholder")}
            placeholderTextColor={colors.muted}
            multiline
            autoFocus
            style={styles.goalInput}
            textAlignVertical="top"
          />
          <Text style={styles.label}>{t("taskNew.attachments")}</Text>
          <View style={styles.attachmentRow}>
            <AppPressable
              accessibilityLabel={t("taskNew.addAttachment")}
              disabled={attachments.length >= MAX_ATTACHMENTS || saving}
              onPress={() => void chooseAttachments()}
              style={styles.attachmentButton}
            >
              <Text style={styles.attachmentButtonText}>{t("taskNew.addAttachment")}</Text>
            </AppPressable>
            <Text style={styles.templateMeta}>{t("taskNew.attachmentCount", { count: attachments.length, max: MAX_ATTACHMENTS })}</Text>
          </View>
          <View style={styles.attachmentRow}>
            <AppPressable
              accessibilityLabel={t("taskNew.addFolder")}
              disabled={Boolean(folder) || attachments.length >= MAX_ATTACHMENTS || saving}
              onPress={() => void chooseFolder()}
              style={styles.attachmentButton}
            >
              <Text style={styles.attachmentButtonText}>{t("taskNew.addFolder")}</Text>
            </AppPressable>
            <Text style={styles.templateMeta}>{t("taskNew.folderHint")}</Text>
          </View>
          {folder ? (
            <View style={styles.folderCard}>
              <Text style={styles.attachmentName} numberOfLines={1}>{folder.rootName}</Text>
              <Text style={styles.templateMeta}>{t("taskNew.folderStats", { count: folder.files.length, size: formatBytes(folder.totalBytes) })}</Text>
              {saving && folderProgress ? <Text style={styles.templateMeta}>{t("taskNew.folderProgress", { completed: folderProgress, total: folder.files.length })}</Text> : null}
              <AppPressable disabled={saving} onPress={() => setFolder(null)} style={styles.attachmentRemove}>
                <Text style={styles.attachmentRemoveText}>{t("taskNew.removeAttachment")}</Text>
              </AppPressable>
            </View>
          ) : null}
          {attachments.map((item, index) => (
            <View key={`${item.uri}:${index}`} style={styles.attachmentRow}>
              <Text style={styles.attachmentName} numberOfLines={1}>{item.name}</Text>
              <AppPressable
                accessibilityLabel={t("taskNew.removeAttachment")}
                disabled={saving}
                onPress={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                style={styles.attachmentRemove}
              >
                <Text style={styles.attachmentRemoveText}>{t("taskNew.removeAttachment")}</Text>
              </AppPressable>
            </View>
          ))}
          <AgentSelector
            agents={gateway.agents}
            selectedAgentId={agentId}
            disabled={saving}
            label={t("agent.selectTask")}
            lockedLabel={t("agent.lockedTask")}
            onChange={setAgentId}
          />
          <TaskLaunchEditor policy={launchPolicy} onChange={setLaunchPolicy} mcpResources={mcpResources} />
          <View style={styles.notificationCard}>
            <Text style={styles.launchTitle}>{t("taskNew.notifyMe")}</Text>
            <Toggle label={t("taskNew.completed")} value={notifyCompleted} onChange={setNotifyCompleted} />
            <Toggle label={t("taskNew.failed")} value={notifyFailed} onChange={setNotifyFailed} />
            <Toggle label={t("taskNew.approval")} value={notifyApproval} onChange={setNotifyApproval} />
            <Text style={styles.launchText}>{t("taskNew.notifyScopeHint")}</Text>
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <AppPressable
            accessibilityRole="button"
            accessibilityLabel={launchPolicy.kind === "immediate" ? t("taskNew.createAndStart") : t("taskNew.create")}
            disabled={!goal.trim() || saving || switchingNode || !selectedNodeId || selectedNodeId !== gateway.nodeId || Boolean(gateway.requiredUpdate) || !isLaunchPolicyValid(launchPolicy)}
            onPress={() => void create()}
            style={[styles.primary, (!goal.trim() || saving || gateway.requiredUpdate || !isLaunchPolicyValid(launchPolicy)) && styles.disabled]}
          >
            {saving ? <ActivityIndicator color="white" /> : <Text style={styles.primaryText}>{launchPolicy.kind === "immediate" ? t("taskNew.createAndStart") : t("taskNew.create")}</Text>}
          </AppPressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange(value: boolean): void }) {
  return <View style={styles.toggle}><Text style={styles.toggleLabel}>{label}</Text><Switch value={value} onValueChange={onChange} trackColor={{ true: colors.accentSoft }} thumbColor={value ? colors.accent : colors.line} /></View>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function folderErrorMessage(code: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    folder_empty: t("taskNew.folder_empty"),
    folder_file_count_exceeded: t("taskNew.folder_file_count_exceeded"),
    folder_total_size_exceeded: t("taskNew.folder_total_size_exceeded"),
    folder_file_size_exceeded: t("taskNew.folder_file_size_exceeded"),
    folder_path_invalid: t("taskNew.folder_path_invalid"),
  } as Record<string, string>)[code] ?? t("taskNew.folderPickFailed");
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { padding: 16, paddingBottom: 48 },
  card: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 18, gap: 10 },
  nodeHeader: { flexDirection: "row", alignItems: "center", gap: 10 },
  nodeRow: { gap: 8, paddingVertical: 2 },
  nodeChoice: { width: 150, minHeight: 62, padding: 10, borderRadius: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background, gap: 3 },
  nodeChoiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentFaint },
  nodeChoiceText: { color: colors.ink, fontSize: 12, fontWeight: "800" },
  nodeChoiceTextSelected: { color: colors.accent },
  nodeChoiceStatus: { color: colors.muted, fontSize: 10 },
  templateRow: { gap: 9, paddingVertical: 2 },
  template: { width: 156, minHeight: 78, padding: 11, borderRadius: 13, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background, gap: 4 },
  templateSelected: { borderColor: colors.accent, backgroundColor: colors.accentFaint },
  templateTitle: { color: colors.ink, fontSize: 13, fontWeight: "800" },
  templateSelectedText: { color: colors.accent },
  templateDetail: { color: colors.muted, fontSize: 11, lineHeight: 15 },
  templateDetails: { marginTop: 4, padding: 12, borderRadius: 12, backgroundColor: colors.accentSoft, gap: 3 },
  templateDetailsTitle: { color: colors.ink, fontWeight: "800", marginBottom: 2 },
  templateMeta: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  updateRequired: { marginBottom: 12, padding: 14, borderRadius: 12, backgroundColor: colors.dangerSoft, gap: 4 },
  updateRequiredTitle: { color: colors.danger, fontWeight: "700" },
  label: { color: colors.ink, fontWeight: "700", marginTop: 4 },
  titleInput: { minHeight: 46, borderWidth: 1, borderColor: colors.line, borderRadius: 12, paddingHorizontal: 12, color: colors.ink, fontSize: 16 },
  goalInput: { minHeight: 180, borderWidth: 1, borderColor: colors.line, borderRadius: 12, padding: 12, color: colors.ink, fontSize: 16, lineHeight: 23 },
  launchCard: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.accentSoft, gap: 4 },
  launchTitle: { color: colors.ink, fontWeight: "700" },
  launchText: { color: colors.muted, lineHeight: 20 },
  notificationCard: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, gap: 8 },
  attachmentRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  attachmentButton: { minHeight: 38, paddingHorizontal: 13, alignItems: "center", justifyContent: "center", borderRadius: 11, borderWidth: 1, borderColor: colors.accent },
  attachmentButtonText: { color: colors.accent, fontWeight: "800" },
  attachmentName: { color: colors.ink, flex: 1, minWidth: 0 },
  attachmentRemove: { minHeight: 32, paddingHorizontal: 11, alignItems: "center", justifyContent: "center", borderRadius: 10, borderWidth: 1, borderColor: colors.line },
  attachmentRemoveText: { color: colors.muted, fontWeight: "700", fontSize: 12 },
  folderCard: { padding: 12, borderRadius: 12, borderWidth: 1, borderColor: colors.line, gap: 5 },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  toggleLabel: { color: colors.ink },
  error: { color: colors.danger },
  primary: { marginTop: 8, minHeight: 48, borderRadius: 14, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  disabled: { opacity: 0.45 },
  primaryText: { color: "white", fontWeight: "700", fontSize: 16 },
});
