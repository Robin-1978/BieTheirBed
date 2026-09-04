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
import { colors, radii, spacing, shadows, typography } from "@/theme";
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
  const [showAdvanced, setShowAdvanced] = useState(false);
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
        setError(caught instanceof Error && caught.message ? caught.message : t("taskNew.createFailed"));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.flex}
    >
      <ScrollView contentContainerStyle={styles.container}>
        {/* 顶部强制版本更新提示 */}
        {gateway.requiredUpdate ? (
          <AppPressable style={styles.updateRequired} onPress={() => router.replace("/update")}>
            <Text style={styles.updateRequiredTitle}>{t("taskNew.updateRequired")}</Text>
            <Text style={styles.launchText}>{t("taskNew.updateAction")}</Text>
          </AppPressable>
        ) : null}

        {/* 1. 快捷任务模板卡片 */}
        <View style={styles.card}>
          <View style={styles.sectionHeader}>
            <AppIcon name="agent" color={colors.accent} size={18} />
            <Text style={styles.sectionTitle}>{t("taskTemplates.title")}</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.templateRow}>
            {TASK_TEMPLATES.map((template) => {
              const isSelected = selectedTemplate === template.id;
              return (
                <AppPressable
                  key={template.id}
                  style={[styles.template, isSelected && styles.templateSelected]}
                  onPress={() => {
                    setSelectedTemplate(template.id);
                    setTitle(t(template.titleKey));
                    setGoal(t(template.goalKey));
                  }}
                >
                  <Text style={[styles.templateTitle, isSelected && styles.templateSelectedText]}>
                    {t(template.titleKey)}
                  </Text>
                  <Text style={styles.templateDetail} numberOfLines={2}>
                    {t(template.detailKey)}
                  </Text>
                </AppPressable>
              );
            })}
          </ScrollView>

          {activeTemplate ? (
            <View style={styles.templateDetails}>
              <Text style={styles.templateDetailsTitle}>{t(activeTemplate.titleKey)}</Text>
              <View style={styles.chipsRow}>
                <View style={styles.metaChip}><Text style={styles.metaChipText}>{t(activeTemplate.durationKey)}</Text></View>
                <View style={styles.metaChip}><Text style={styles.metaChipText}>{t(activeTemplate.connectionKey)}</Text></View>
              </View>
              <Text style={styles.templateMeta}>{t("taskTemplates.result", { value: t(activeTemplate.resultKey) })}</Text>
            </View>
          ) : null}
        </View>

        {/* 2. 核心任务定义卡片 */}
        <View style={styles.card}>
          <View style={styles.sectionHeader}>
            <AppIcon name="tasks" color={colors.accent} size={18} />
            <Text style={styles.sectionTitle}>{t("taskNew.goal")}</Text>
          </View>
          <Text style={styles.inputSubLabel}>{t("taskNew.name")}</Text>
          <TextInput
            accessibilityLabel={t("taskNew.name")}
            value={title}
            onChangeText={setTitle}
            placeholder={t("taskNew.namePlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.titleInput}
            returnKeyType="next"
          />
          <Text style={styles.inputSubLabel}>{t("taskNew.goal")}</Text>
          <TextInput
            accessibilityLabel={t("taskNew.goal")}
            value={goal}
            onChangeText={setGoal}
            placeholder={t("taskNew.goalPlaceholder")}
            placeholderTextColor={colors.muted}
            multiline
            style={styles.goalInput}
            textAlignVertical="top"
          />

          {/* 附件与文件夹挂载 */}
          <View style={styles.attachmentGroup}>
            <View style={styles.attachmentRow}>
              <AppPressable
                accessibilityLabel={t("taskNew.addAttachment")}
                disabled={attachments.length >= MAX_ATTACHMENTS || saving}
                onPress={() => void chooseAttachments()}
                style={styles.attachmentButton}
              >
                <AppIcon name="file" color={colors.accent} size={16} />
                <Text style={styles.attachmentButtonText}>{t("taskNew.addAttachment")}</Text>
              </AppPressable>
              <AppPressable
                accessibilityLabel={t("taskNew.addFolder")}
                disabled={Boolean(folder) || attachments.length >= MAX_ATTACHMENTS || saving}
                onPress={() => void chooseFolder()}
                style={styles.attachmentButton}
              >
                <AppIcon name="folder" color={colors.accent} size={16} />
                <Text style={styles.attachmentButtonText}>{t("taskNew.addFolder")}</Text>
              </AppPressable>
            </View>

            {folder ? (
              <View style={styles.folderCard}>
                <Text style={styles.attachmentName} numberOfLines={1}>{folder.rootName}</Text>
                <Text style={styles.templateMeta}>
                  {t("taskNew.folderStats", { count: folder.files.length, size: formatBytes(folder.totalBytes) })}
                </Text>
                {saving && folderProgress ? (
                  <Text style={styles.templateMeta}>
                    {t("taskNew.folderProgress", { completed: folderProgress, total: folder.files.length })}
                  </Text>
                ) : null}
                <AppPressable disabled={saving} onPress={() => setFolder(null)} style={styles.attachmentRemove}>
                  <Text style={styles.attachmentRemoveText}>{t("taskNew.removeAttachment")}</Text>
                </AppPressable>
              </View>
            ) : null}

            {attachments.map((item, index) => (
              <View key={`${item.uri}:${index}`} style={styles.attachmentItemRow}>
                <Text style={styles.attachmentName} numberOfLines={1}>{item.name}</Text>
                <AppPressable
                  accessibilityLabel={t("taskNew.removeAttachment")}
                  disabled={saving}
                  onPress={() => setAttachments((current) => current.filter((_, i) => i !== index))}
                  style={styles.attachmentRemove}
                >
                  <AppIcon name="x" color={colors.muted} size={16} />
                </AppPressable>
              </View>
            ))}
          </View>
        </View>

        {/* 3. 执行环境与智能体卡片 */}
        <View style={styles.card}>
          <View style={styles.sectionHeader}>
            <AppIcon name="node" color={colors.accent} size={18} />
            <Text style={styles.sectionTitle}>{t("taskNew.executionNode")}</Text>
          </View>
          {gateway.nodes.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.nodeRow}>
              {gateway.nodes.map((node) => {
                const isSelected = selectedNodeId === node.nodeId;
                return (
                  <AppPressable
                    key={node.nodeId}
                    style={[styles.nodeChoice, isSelected && styles.nodeChoiceSelected]}
                    onPress={() => void chooseNode(node.nodeId)}
                    disabled={switchingNode}
                  >
                    <View style={styles.nodeTopRow}>
                      <AppIcon name="node" color={isSelected ? colors.accent : colors.muted} size={16} />
                      <Text style={styles.nodeChoiceStatus}>
                        {node.nodeId === gateway.nodeId && gateway.status === "ready" ? t("taskNew.nodeReady") : t("taskNew.nodeAvailable")}
                      </Text>
                    </View>
                    <Text style={[styles.nodeChoiceText, isSelected && styles.nodeChoiceTextSelected]} numberOfLines={1}>
                      {presentNodeName(node, t("common.unnamedComputer"))}
                    </Text>
                  </AppPressable>
                );
              })}
            </ScrollView>
          ) : (
            <Text style={styles.templateMeta}>{t("taskNew.noNode")}</Text>
          )}

          <AgentSelector
            agents={gateway.agents}
            selectedAgentId={agentId}
            disabled={saving}
            label={t("agent.selectTask")}
            lockedLabel={t("agent.lockedTask")}
            onChange={setAgentId}
          />
        </View>

        {/* 4. 高级调度与通知设置 (可折叠) */}
        <View style={styles.card}>
          <AppPressable
            style={styles.advancedToggle}
            onPress={() => setShowAdvanced(!showAdvanced)}
          >
            <View style={styles.sectionHeader}>
              <AppIcon name="settings" color={colors.muted} size={18} />
              <Text style={styles.sectionTitle}>{t("nav.settings")}</Text>
            </View>
            <AppIcon name={showAdvanced ? "chevron-up" : "chevron-down"} color={colors.muted} size={18} />
          </AppPressable>

          {showAdvanced ? (
            <View style={styles.advancedContent}>
              <TaskLaunchEditor policy={launchPolicy} onChange={setLaunchPolicy} mcpResources={mcpResources} />
              <View style={styles.notificationCard}>
                <Text style={styles.launchTitle}>{t("taskNew.notifyMe")}</Text>
                <Toggle label={t("taskNew.completed")} value={notifyCompleted} onChange={setNotifyCompleted} />
                <Toggle label={t("taskNew.failed")} value={notifyFailed} onChange={setNotifyFailed} />
                <Toggle label={t("taskNew.approval")} value={notifyApproval} onChange={setNotifyApproval} />
                <Text style={styles.launchText}>{t("taskNew.notifyScopeHint")}</Text>
              </View>
            </View>
          ) : null}
        </View>

        {error ? <Text style={styles.error}>{error}</Text> : null}

        {/* 底部主操作按钮 */}
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={launchPolicy.kind === "immediate" ? t("taskNew.createAndStart") : t("taskNew.create")}
          disabled={!goal.trim() || saving || switchingNode || !selectedNodeId || selectedNodeId !== gateway.nodeId || Boolean(gateway.requiredUpdate) || !isLaunchPolicyValid(launchPolicy)}
          onPress={() => void create()}
          style={[styles.primary, (!goal.trim() || saving || gateway.requiredUpdate || !isLaunchPolicyValid(launchPolicy)) && styles.disabled]}
        >
          {saving ? (
            <ActivityIndicator color={colors.onAccent} />
          ) : (
            <Text style={styles.primaryText}>
              {launchPolicy.kind === "immediate" ? t("taskNew.createAndStart") : t("taskNew.create")}
            </Text>
          )}
        </AppPressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange(value: boolean): void }) {
  return (
    <View style={styles.toggle}>
      <Text style={styles.toggleLabel}>{label}</Text>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ true: colors.accentSoft }}
        thumbColor={value ? colors.accent : colors.line}
      />
    </View>
  );
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
  container: {
    padding: spacing.large,
    gap: spacing.medium,
    paddingBottom: 48,
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
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "800",
  },
  inputSubLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    marginTop: spacing.xsmall,
  },
  titleInput: {
    minHeight: 44,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
    paddingHorizontal: spacing.medium,
    color: colors.ink,
    fontSize: 14,
    fontWeight: "600",
  },
  goalInput: {
    minHeight: 110,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
    padding: spacing.medium,
    color: colors.ink,
    fontSize: 14,
    lineHeight: 20,
  },
  templateRow: {
    gap: spacing.small,
    paddingVertical: 4,
  },
  template: {
    width: 160,
    minHeight: 82,
    padding: spacing.medium,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
    gap: 4,
  },
  templateSelected: {
    borderColor: colors.accent,
    backgroundColor: colors.accentFaint,
  },
  templateTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "800",
  },
  templateSelectedText: {
    color: colors.accent,
  },
  templateDetail: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 15,
  },
  templateDetails: {
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    gap: spacing.xsmall,
  },
  templateDetailsTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "800",
  },
  chipsRow: {
    flexDirection: "row",
    gap: spacing.small,
    marginVertical: 4,
  },
  metaChip: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: radii.small,
    backgroundColor: colors.accentSoft,
  },
  metaChipText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
  },
  templateMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16,
  },
  nodeRow: {
    gap: spacing.small,
    paddingVertical: 2,
  },
  nodeChoice: {
    width: 140,
    minHeight: 60,
    padding: spacing.medium,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
    gap: 4,
  },
  nodeTopRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  nodeChoiceSelected: {
    borderColor: colors.accent,
    backgroundColor: colors.accentFaint,
  },
  nodeChoiceText: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "800",
  },
  nodeChoiceTextSelected: {
    color: colors.accent,
  },
  nodeChoiceStatus: {
    color: colors.muted,
    fontSize: 10,
  },
  attachmentGroup: {
    gap: spacing.small,
    paddingTop: spacing.xsmall,
  },
  attachmentRow: {
    flexDirection: "row",
    gap: spacing.medium,
  },
  attachmentButton: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
  },
  attachmentButtonText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  attachmentItemRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: spacing.small,
    borderRadius: radii.small,
    backgroundColor: colors.background,
  },
  attachmentName: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "600",
    flex: 1,
  },
  attachmentRemove: {
    padding: 4,
  },
  attachmentRemoveText: {
    color: colors.danger,
    fontSize: 11,
    fontWeight: "700",
  },
  folderCard: {
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    gap: 4,
  },
  advancedToggle: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  advancedContent: {
    gap: spacing.medium,
    paddingTop: spacing.small,
  },
  notificationCard: {
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    padding: spacing.medium,
    gap: spacing.small,
  },
  launchTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "800",
  },
  launchText: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 15,
  },
  toggle: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 36,
  },
  toggleLabel: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "600",
  },
  primary: {
    minHeight: 48,
    borderRadius: radii.medium,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryText: {
    color: colors.onAccent,
    fontSize: 15,
    fontWeight: "800",
  },
  disabled: {
    opacity: 0.45,
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    textAlign: "center",
  },
  updateRequired: {
    marginBottom: spacing.medium,
    padding: spacing.large,
    borderRadius: radii.medium,
    backgroundColor: colors.dangerSoft,
    gap: spacing.xsmall,
  },
  updateRequiredTitle: {
    color: colors.danger,
    fontWeight: "700",
  },
});
