import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useState } from "react";
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

import type { MCPResourceCatalogItem, Task, TaskLaunchPolicy } from "@/api/models";
import { immediatePolicy, isLaunchPolicyValid, TaskLaunchEditor } from "@/components/TaskLaunchEditor";
import { AsyncStateView } from "@/components/AsyncStateView";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { useI18n } from "@/i18n";
import { AppPressable } from "@/components/AppPressable";

export default function EditTaskScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const taskId = String(id ?? "");
  const gateway = useGateway();
  const { t } = useI18n();
  const [task, setTask] = useState<Task | null>(null);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notifyCompleted, setNotifyCompleted] = useState(true);
  const [notifyFailed, setNotifyFailed] = useState(true);
  const [notifyApproval, setNotifyApproval] = useState(true);
  const [launchPolicy, setLaunchPolicy] = useState<TaskLaunchPolicy>(immediatePolicy);
  const [mcpResources, setMcpResources] = useState<MCPResourceCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);

  const reloadTask = useCallback(() => {
    if (!gateway.client || !taskId) return;
    setLoading(true);
    setError("");
    void gateway.runAuthenticated((client) => client.getTask(taskId))
      .then((loaded) => {
        setTask(loaded);
        setTitle(loaded.title);
        setGoal(loaded.goal);
        setNotifyCompleted(loaded.notification_policy.completed ?? true);
        setNotifyFailed(loaded.notification_policy.failed ?? true);
        setNotifyApproval(loaded.notification_policy.waiting_approval ?? true);
        setLaunchPolicy(loaded.launch_policy);
      })
      .catch(() => setError(t("taskEdit.loadFailed")))
      .finally(() => setLoading(false));
  }, [gateway.client, gateway.runAuthenticated, t, taskId]);

  useEffect(() => {
    if (!gateway.client) return;
    void gateway.runAuthenticated((client) => client.listMcpResources())
      .then(setMcpResources)
      .catch(() => setMcpResources([]));
  }, [gateway.client, gateway.runAuthenticated]);

  useEffect(() => {
    reloadTask();
  }, [reloadTask]);

  async function save() {
    if (!task || !goal.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      const updated = await gateway.runAuthenticated((client) => client.updateTask(task.task_id, {
        title: title.trim(),
        goal: goal.trim(),
        notificationPolicy: {
          ...task.notification_policy,
          completed: notifyCompleted,
          failed: notifyFailed,
          waiting_approval: notifyApproval,
        },
        launchPolicy,
        expectedRevision: task.revision,
      }));
      router.back();
    } catch {
      setError(t("taskEdit.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!task && loading) {
    return <View style={styles.loading}><AsyncStateView state="loading" /></View>;
  }

  if (!task) {
    return (
      <View style={styles.loading}>
        <AsyncStateView
          state="error"
          message={error || t("taskEdit.loadFailed")}
          retryLabel={t("tasks.reload")}
          onRetry={reloadTask}
        />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.note}>{t("taskEdit.note")}</Text>
        <Text style={styles.label}>{t("taskNew.name")}</Text>
        <TextInput value={title} onChangeText={setTitle} style={styles.titleInput} accessibilityLabel={t("taskNew.name")} />
        <Text style={styles.label}>{t("taskNew.goal")}</Text>
        <TextInput
          value={goal}
          onChangeText={setGoal}
          multiline
          textAlignVertical="top"
          style={styles.goalInput}
          accessibilityLabel={t("taskNew.goal")}
        />
        <TaskLaunchEditor policy={launchPolicy} onChange={setLaunchPolicy} mcpResources={mcpResources} />
        <View style={styles.notificationCard}>
          <Text style={styles.label}>{t("taskNew.notifyMe")}</Text>
          <Toggle label={t("taskNew.completed")} value={notifyCompleted} onChange={setNotifyCompleted} />
          <Toggle label={t("taskNew.failed")} value={notifyFailed} onChange={setNotifyFailed} />
          <Toggle label={t("taskNew.approval")} value={notifyApproval} onChange={setNotifyApproval} />
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <AppPressable
          disabled={!task || !goal.trim() || saving || !isLaunchPolicyValid(launchPolicy)}
          onPress={() => void save()}
          style={[styles.save, (!task || !goal.trim() || saving || !isLaunchPolicyValid(launchPolicy)) && styles.disabled]}
        >
          {saving ? <ActivityIndicator color="white" /> : <Text style={styles.saveText}>{t("taskEdit.save")}</Text>}
        </AppPressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange(value: boolean): void }) {
  return <View style={styles.toggle}><Text style={styles.toggleLabel}>{label}</Text><Switch value={value} onValueChange={onChange} trackColor={{ true: colors.accentSoft }} thumbColor={value ? colors.accent : colors.line} /></View>;
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  loading: { flex: 1, alignItems: "center", justifyContent: "center" },
  container: { padding: spacing.xlarge, gap: spacing.medium, paddingBottom: 48 },
  note: { color: colors.muted, backgroundColor: colors.accentSoft, padding: spacing.large, borderRadius: radii.medium, lineHeight: 21, marginBottom: spacing.xsmall },
  label: { color: colors.ink, ...typography.caption, fontWeight: "700", marginTop: spacing.small },
  titleInput: { minHeight: 48, borderWidth: 1, borderColor: colors.line, borderRadius: radii.medium, backgroundColor: colors.surface, paddingHorizontal: spacing.medium, color: colors.ink, fontSize: 16 },
  goalInput: { minHeight: 220, borderWidth: 1, borderColor: colors.line, borderRadius: radii.medium, backgroundColor: colors.surface, padding: spacing.medium, color: colors.ink, fontSize: 16, lineHeight: 23 },
  notificationCard: { marginTop: spacing.small, padding: spacing.large, borderRadius: radii.medium, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, gap: spacing.small , ...shadows.card },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  toggleLabel: { color: colors.ink },
  error: { color: colors.danger, lineHeight: 21 },
  save: { marginTop: spacing.small, minHeight: 48, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accent },
  saveText: { color: "white", ...typography.subheading, fontWeight: "700" },
  disabled: { opacity: 0.45 },
});
