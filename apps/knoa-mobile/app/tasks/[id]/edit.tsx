import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Switch,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { Task, TaskLaunchPolicy } from "@/api/models";
import { immediatePolicy, isLaunchPolicyValid, TaskLaunchEditor } from "@/components/TaskLaunchEditor";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function EditTaskScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const taskId = String(id ?? "");
  const gateway = useGateway();
  const [task, setTask] = useState<Task | null>(null);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notifyCompleted, setNotifyCompleted] = useState(true);
  const [notifyFailed, setNotifyFailed] = useState(true);
  const [notifyApproval, setNotifyApproval] = useState(true);
  const [launchPolicy, setLaunchPolicy] = useState<TaskLaunchPolicy>(immediatePolicy);

  useEffect(() => {
    if (!gateway.client || !taskId) return;
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
      .catch(() => setError("任务加载失败"));
  }, [gateway.client, gateway.runAuthenticated, taskId]);

  async function save() {
    if (!task || !goal.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      await gateway.runAuthenticated((client) => client.updateTask(task.task_id, {
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
      setError("保存失败，任务可能已在其他设备修改，请返回刷新后重试");
    } finally {
      setSaving(false);
    }
  }

  if (!task && !error) return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.note}>修改只影响以后创建的执行，不会改写已有历史。</Text>
        <Text style={styles.label}>任务名称</Text>
        <TextInput value={title} onChangeText={setTitle} style={styles.titleInput} accessibilityLabel="任务名称" />
        <Text style={styles.label}>目标</Text>
        <TextInput
          value={goal}
          onChangeText={setGoal}
          multiline
          textAlignVertical="top"
          style={styles.goalInput}
          accessibilityLabel="任务目标"
        />
        <TaskLaunchEditor policy={launchPolicy} onChange={setLaunchPolicy} />
        <View style={styles.notificationCard}>
          <Text style={styles.label}>通知我</Text>
          <Toggle label="任务完成" value={notifyCompleted} onChange={setNotifyCompleted} />
          <Toggle label="任务失败" value={notifyFailed} onChange={setNotifyFailed} />
          <Toggle label="需要确认" value={notifyApproval} onChange={setNotifyApproval} />
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable
          disabled={!task || !goal.trim() || saving || !isLaunchPolicyValid(launchPolicy)}
          onPress={() => void save()}
          style={[styles.save, (!task || !goal.trim() || saving || !isLaunchPolicyValid(launchPolicy)) && styles.disabled]}
        >
          {saving ? <ActivityIndicator color="white" /> : <Text style={styles.saveText}>保存修改</Text>}
        </Pressable>
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
  container: { padding: 18, gap: 10, paddingBottom: 48 },
  note: { color: colors.muted, backgroundColor: colors.accentSoft, padding: 14, borderRadius: 12, lineHeight: 21, marginBottom: 4 },
  label: { color: colors.ink, fontWeight: "700", marginTop: 6 },
  titleInput: { minHeight: 48, borderWidth: 1, borderColor: colors.line, borderRadius: 12, backgroundColor: colors.surface, paddingHorizontal: 12, color: colors.ink, fontSize: 16 },
  goalInput: { minHeight: 220, borderWidth: 1, borderColor: colors.line, borderRadius: 12, backgroundColor: colors.surface, padding: 12, color: colors.ink, fontSize: 16, lineHeight: 23 },
  notificationCard: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, gap: 8 },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  toggleLabel: { color: colors.ink },
  error: { color: colors.danger, lineHeight: 21 },
  save: { marginTop: 8, minHeight: 48, alignItems: "center", justifyContent: "center", borderRadius: 14, backgroundColor: colors.accent },
  saveText: { color: "white", fontWeight: "700", fontSize: 16 },
  disabled: { opacity: 0.45 },
});
