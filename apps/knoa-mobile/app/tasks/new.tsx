import { router, useLocalSearchParams } from "expo-router";
import * as Crypto from "expo-crypto";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Switch,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { useConnection, useFleet, useSession } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { immediatePolicy, isLaunchPolicyValid, TaskLaunchEditor } from "@/components/TaskLaunchEditor";
import { AgentSelector } from "@/components/AgentSelector";
import { TemplatePickerCard } from "@/components/TemplatePickerCard";
import type { MCPResourceCatalogItem, TaskLaunchPolicy } from "@/api/models";
import { useI18n } from "@/i18n";
import { AppPressable } from "@/components/AppPressable";
import { AppIcon } from "@/components/AppIcon";
import { TASK_TEMPLATES, shouldConfirmTemplateOverwrite, type TaskTemplate } from "@/taskTemplates";
import { parseScheduleFromPrompt } from "@/nlSchedule";
import { recommendNodeId } from "@/models/nodeRecommendation";
import { listHubNodes } from "@/hub/hubClient";
import { enqueueOfflineTask } from "@/storage/offlineTaskQueue";
import { requestTaskNotificationPermission } from "@/notifications/taskNotifications";
import { presentNodeName } from "@/presentation/nodePresentation";
import { MAX_ATTACHMENTS, pickAttachments, type PickedAttachment } from "@/media/attachmentPicker";
import { uploadSessionAttachments } from "@/api/uploadAttachments";
import { pickFolderSnapshot, uploadFolderSnapshot, type FolderSelection } from "@/media/folderManifest";

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

function formatClock(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

export default function NewTaskScreen() {
  const gateway = useSession();
  const { status } = useConnection();
  const { agents, defaultAgentId, nodeId, nodes, requiredUpdate, switchNode } = useFleet();
  const { t } = useI18n();
  const params = useLocalSearchParams<{
    template?: string;
    title?: string;
    goal?: string;
    agentId?: string;
    workspaceId?: string;
    workspaceName?: string;
    nodeId?: string;
    recurring?: string;
  }>();
  const [title, setTitle] = useState(stringParam(params.title));
  const [goal, setGoal] = useState(stringParam(params.goal));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notifyCompleted, setNotifyCompleted] = useState(true);
  const [notifyFailed, setNotifyFailed] = useState(true);
  const [notifyApproval, setNotifyApproval] = useState(true);
  const [launchPolicy, setLaunchPolicy] = useState<TaskLaunchPolicy>(immediatePolicy);
  const [agentId, setAgentId] = useState(stringParam(params.agentId) || defaultAgentId || "knoa");
  const [mcpResources, setMcpResources] = useState<MCPResourceCatalogItem[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState(nodeId || stringParam(params.nodeId) || "");
  const [switchingNode, setSwitchingNode] = useState(false);
  const [hubOnlineIds, setHubOnlineIds] = useState<string[] | null>(null);
  const [attachments, setAttachments] = useState<PickedAttachment[]>([]);
  const [folder, setFolder] = useState<FolderSelection | null>(null);
  const [folderProgress, setFolderProgress] = useState(0);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [nlPrompt, setNlPrompt] = useState("");
  const [nlSuccessMessage, setNlSuccessMessage] = useState("");
  const requestIdentity = useRef<{ fingerprint: string; requestId: string } | null>(null);

  function parseNaturalLanguagePrompt() {
    const raw = nlPrompt.trim();
    if (!raw) return;
    setNlSuccessMessage("");

    // 智能提取：若输入包含明确换行或分隔符，取首行作为标题
    const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    let extractedTitle = "";
    let extractedGoal = raw;

    if (lines.length > 1) {
      extractedTitle = lines[0]!.slice(0, 30);
      extractedGoal = lines.slice(1).join("\n");
    } else {
      // 提取核心句作为标题（过滤常用引导词）
      const cleanSummary = raw
        .replace(/^(请帮我|麻烦|帮我|请|每天|每小时|实时|立刻|定时)/, "")
        .trim();
      extractedTitle = cleanSummary.length > 20 ? `${cleanSummary.slice(0, 20)}…` : cleanSummary;
    }

    setTitle(extractedTitle || raw.slice(0, 20));
    setGoal(extractedGoal);
    const schedule = parseScheduleFromPrompt(raw);
    if (schedule) {
      setLaunchPolicy(schedule.policy);
      if (schedule.kind === "daily") {
        setNlSuccessMessage(
          t("taskNew.nlScheduleDaily", { time: formatClock(schedule.hour, schedule.minute) }),
        );
      } else if (schedule.kind === "weekly") {
        setNlSuccessMessage(
          t("taskNew.nlScheduleWeekly", { time: formatClock(schedule.hour, schedule.minute) }),
        );
      } else {
        setNlSuccessMessage(
          t("taskNew.nlScheduleInterval", { minutes: Math.round(schedule.intervalSeconds / 60) }),
        );
      }
      return;
    }
    setNlSuccessMessage(t("taskNew.nlSuccess"));
  }

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

  useEffect(() => {
    let active = true;
    void listHubNodes()
      .then((directory) => {
        if (active) setHubOnlineIds(directory.filter((node) => node.online).map((node) => node.node_id));
      })
      .catch(() => {
        if (active) setHubOnlineIds(null);
      });
    return () => { active = false; };
  }, []);

  const recommendedNodeId = useMemo(
    () => recommendNodeId(nodes.map((node) => node.nodeId), hubOnlineIds, nodeId),
    [nodes, nodeId, hubOnlineIds],
  );
  const recommendedNode = nodes.find((node) => node.nodeId === recommendedNodeId) ?? null;

  useEffect(() => {
    const paramTitle = stringParam(params.title);
    const paramGoal = stringParam(params.goal);
    const paramAgentId = stringParam(params.agentId);
    if (paramTitle) setTitle(paramTitle);
    if (paramGoal) setGoal(paramGoal);
    if (paramAgentId) setAgentId(paramAgentId);

    const templateId = stringParam(params.template);
    const requested = TASK_TEMPLATES.find((template) => template.id === templateId);
    if (!requested) return;
    setSelectedTemplate(requested.id);
    setTitle(t(requested.titleKey));
    // 场景入口自带目标描述时保留它，只借模板的结构（标题/预检说明）。
    if (!paramGoal) setGoal(t(requested.goalKey));
  }, [params.agentId, params.goal, params.template, params.title, t]);

  // "Watch for me" entry from the chat deck: preset a daily interval.
  // Notification switches already default to on, closing the push loop.
  useEffect(() => {
    if (stringParam(params.recurring) === "daily") {
      setLaunchPolicy({
        ...immediatePolicy(),
        kind: "scheduled",
        schedule_type: "interval",
        interval_seconds: 86400,
      });
    }
  }, [params.recurring]);

  useEffect(() => {
    if (nodeId) setSelectedNodeId(nodeId);
  }, [nodeId]);

  function applyTemplate(template: TaskTemplate) {
    const apply = () => {
      setSelectedTemplate(template.id);
      setTitle(t(template.titleKey));
      setGoal(t(template.goalKey));
    };
    if (shouldConfirmTemplateOverwrite(title, goal)) {
      Alert.alert(
        t("taskNew.templateOverwriteTitle"),
        t("taskNew.templateOverwriteMessage"),
        [
          { text: t("common.cancel"), style: "cancel" },
          { text: t("taskNew.templateOverwriteApply"), style: "destructive", onPress: apply },
        ],
      );
      return;
    }
    apply();
  }

  async function chooseNode(targetNodeId: string) {
    if (!targetNodeId || targetNodeId === nodeId || switchingNode) return;
    setSwitchingNode(true);
    setError("");
    try {
      await switchNode(targetNodeId);
      setSelectedNodeId(targetNodeId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("taskNew.nodeSwitchFailed"));
    } finally {
      setSwitchingNode(false);
    }
  }

  // 原位预检：按钮只变灰不说话是最伤的，把阻断原因逐条摆出来。
  const blockers = useMemo(() => {
    const items: string[] = [];
    if (!goal.trim()) items.push(t("taskNew.blockerGoal"));
    if (!selectedNodeId) items.push(t("taskNew.blockerNode"));
    else if (switchingNode) items.push(t("taskNew.blockerSwitching"));
    if (requiredUpdate) items.push(t("taskNew.blockerUpdate"));
    if (goal.trim() && !isLaunchPolicyValid(launchPolicy)) items.push(t("taskNew.blockerPolicy"));
    return items;
  }, [goal, launchPolicy, nodeId, requiredUpdate, selectedNodeId, switchingNode, t]);

  async function create() {
    if (requiredUpdate) {
      router.replace("/update");
      return;
    }
    const normalizedGoal = goal.trim();
    if (!normalizedGoal || saving) return;
    setSaving(true);
    setError("");
    try {
      // Node invisibility: switch silently on create instead of blocking.
      if (selectedNodeId && selectedNodeId !== nodeId) {
        setSwitchingNode(true);
        try {
          await switchNode(selectedNodeId);
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : t("taskNew.nodeSwitchFailed"));
          return;
        } finally {
          setSwitchingNode(false);
        }
      }
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
      if (status !== "ready") {
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
        {requiredUpdate ? (
          <AppPressable style={styles.updateRequired} onPress={() => router.replace("/update")}>
            <Text style={styles.updateRequiredTitle}>{t("taskNew.updateRequired")}</Text>
            <Text style={styles.launchText}>{t("taskNew.updateAction")}</Text>
          </AppPressable>
        ) : null}

        {/* 1. 自然语言一句话建任务解析器 */}
        <View style={styles.card}>
          <View style={styles.sectionHeader}>
            <AppIcon name="agent" color={colors.accent} size={18} />
            <Text style={styles.sectionTitle}>{t("taskNew.nlTitle")}</Text>
          </View>
          <TextInput
            accessibilityLabel={t("taskNew.nlTitle")}
            value={nlPrompt}
            onChangeText={(val) => {
              setNlPrompt(val);
              if (nlSuccessMessage) setNlSuccessMessage("");
            }}
            placeholder={t("taskNew.nlPlaceholder")}
            placeholderTextColor={colors.muted}
            multiline
            numberOfLines={3}
            style={styles.nlInput}
          />
          <View style={styles.nlActionRow}>
            {nlSuccessMessage ? (
              <Text style={styles.nlSuccessText} numberOfLines={1}>{nlSuccessMessage}</Text>
            ) : <View style={styles.flex} />}
            <AppPressable
              disabled={!nlPrompt.trim()}
              style={[styles.nlParseButton, !nlPrompt.trim() && styles.nlParseButtonDisabled]}
              onPress={parseNaturalLanguagePrompt}
            >
              <AppIcon name="agent" color={colors.onAccent} size={14} />
              <Text style={styles.nlParseButtonText}>{t("taskNew.nlParse")}</Text>
            </AppPressable>
          </View>
        </View>

        {/* 2. 快捷任务模板卡片 */}
        <TemplatePickerCard selectedTemplate={selectedTemplate} onSelect={applyTemplate} />

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
          {recommendedNode ? (
            <Text style={styles.recommend}>
              {recommendedNode.nodeId === nodeId && status === "ready"
                ? t("taskNew.recommendCurrent", { name: presentNodeName(recommendedNode, t("common.unnamedComputer")) })
                : hubOnlineIds?.includes(recommendedNode.nodeId)
                  ? t("taskNew.recommendSwitch", { name: presentNodeName(recommendedNode, t("common.unnamedComputer")) })
                  : t("taskNew.recommendOffline", { name: presentNodeName(recommendedNode, t("common.unnamedComputer")) })}
            </Text>
          ) : null}
          {nodes.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.nodeRow}>
              {nodes.map((node) => {
                const isSelected = selectedNodeId === node.nodeId;
                const isCurrentReady = node.nodeId === nodeId && status === "ready";
                const isOffline = hubOnlineIds !== null && !hubOnlineIds.includes(node.nodeId) && !isCurrentReady;
                return (
                  <AppPressable
                    key={node.nodeId}
                    style={[styles.nodeChoice, isSelected && styles.nodeChoiceSelected]}
                    onPress={() => void chooseNode(node.nodeId)}
                    disabled={switchingNode}
                  >
                    <View style={styles.nodeTopRow}>
                      {node.nodeId === recommendedNodeId ? (
                        <View style={styles.recommendBadge}>
                          <Text style={styles.recommendBadgeText}>{t("taskNew.recommended")}</Text>
                        </View>
                      ) : null}
                      <AppIcon name="node" color={isSelected ? colors.accent : colors.muted} size={16} />
                      <Text style={styles.nodeChoiceStatus}>
                        {isCurrentReady ? t("taskNew.nodeReady") : isOffline ? t("taskNew.nodeOffline") : t("taskNew.nodeAvailable")}
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
            agents={agents}
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
        {blockers.length && !saving ? (
          <View style={styles.blockers}>
            {blockers.map((item) => (
              <Text key={item} style={styles.blockerText}>• {item}</Text>
            ))}
          </View>
        ) : null}

        {/* 底部主操作按钮 */}
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={launchPolicy.kind === "immediate" ? t("taskNew.createAndStart") : t("taskNew.create")}
          disabled={!goal.trim() || saving || switchingNode || !selectedNodeId || Boolean(requiredUpdate) || !isLaunchPolicyValid(launchPolicy)}
          onPress={() => void create()}
          style={[styles.primary, (!goal.trim() || saving || requiredUpdate || !isLaunchPolicyValid(launchPolicy)) && styles.disabled]}
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
    fontWeight: "700",
  },
  nlInput: {
    minHeight: 72,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
    padding: spacing.medium,
    color: colors.ink,
    fontSize: 15,
    lineHeight: 20,
  },
  nlActionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.small,
  },
  nlSuccessText: {
    flex: 1,
    color: colors.success,
    fontSize: 12,
    fontWeight: "700",
  },
  nlParseButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.accent,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
  },
  nlParseButtonDisabled: {
    opacity: 0.5,
  },
  nlParseButtonText: {
    color: colors.onAccent,
    fontSize: 12,
    fontWeight: "700",
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
    fontSize: 15,
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
    fontSize: 15,
    lineHeight: 20,
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
    fontWeight: "700",
  },
  nodeChoiceTextSelected: {
    color: colors.accent,
  },
  nodeChoiceStatus: {
    color: colors.muted,
    fontSize: 11,
  },
  recommend: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "600",
    lineHeight: 19,
  },
  recommendBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radii.small,
    backgroundColor: colors.accent,
  },
  recommendBadgeText: {
    color: colors.onAccent,
    fontSize: 10,
    fontWeight: "800",
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
    fontWeight: "700",
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
    fontWeight: "700",
  },
  disabled: {
    opacity: 0.45,
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    textAlign: "center",
  },
  blockers: {
    gap: 2,
    paddingHorizontal: spacing.medium,
  },
  blockerText: {
    color: colors.warning,
    fontSize: 12,
    lineHeight: 17,
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
