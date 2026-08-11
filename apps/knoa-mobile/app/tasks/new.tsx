import { router } from "expo-router";
import * as Notifications from "expo-notifications";
import * as Crypto from "expo-crypto";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
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

import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { immediatePolicy, isLaunchPolicyValid, TaskLaunchEditor } from "@/components/TaskLaunchEditor";
import type { TaskLaunchPolicy } from "@/api/models";
import { registerPush } from "@/notifications";

export default function NewTaskScreen() {
  const gateway = useGateway();
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notifyCompleted, setNotifyCompleted] = useState(true);
  const [notifyFailed, setNotifyFailed] = useState(true);
  const [notifyApproval, setNotifyApproval] = useState(true);
  const [launchPolicy, setLaunchPolicy] = useState<TaskLaunchPolicy>(immediatePolicy);
  const requestIdentity = useRef<{ fingerprint: string; requestId: string } | null>(null);

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
      const input = {
        title: title.trim(),
        goal: normalizedGoal,
        notificationPolicy: {
          completed: notifyCompleted,
          failed: notifyFailed,
          waiting_approval: notifyApproval,
        },
        launchPolicy,
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
      if (launchPolicy.kind !== "immediate" && gateway.client) {
        const permission = await Notifications.getPermissionsAsync();
        if (permission.status !== "granted") {
          Alert.alert(
            "及时知道任务结果",
            "定时或事件任务会在后台运行。允许通知后，小诺可以在完成、失败或需要确认时提醒你。",
            [
              { text: "稍后", onPress: () => router.replace(`/tasks/${result.task.task_id}`) },
              {
                text: "启用通知",
                onPress: () => void registerPush(gateway.client!, true)
                  .catch(() => undefined)
                  .finally(() => router.replace(`/tasks/${result.task.task_id}`)),
              },
            ],
          );
          return;
        }
      }
      router.replace(`/tasks/${result.task.task_id}`);
    } catch {
      setError("任务创建失败，请检查连接后重试");
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
          <Pressable style={styles.updateRequired} onPress={() => router.replace("/update")}>
            <Text style={styles.updateRequiredTitle}>需要更新小诺后才能创建任务</Text>
            <Text style={styles.launchText}>点击前往版本页面下载安装。</Text>
          </Pressable>
        ) : null}
        <View style={styles.card}>
          <Text style={styles.label}>任务名称</Text>
          <TextInput
            accessibilityLabel="任务名称"
            value={title}
            onChangeText={setTitle}
            placeholder="可不填，将从目标自动生成"
            placeholderTextColor={colors.muted}
            style={styles.titleInput}
            returnKeyType="next"
          />
          <Text style={styles.label}>目标</Text>
          <TextInput
            accessibilityLabel="任务目标"
            value={goal}
            onChangeText={setGoal}
            placeholder="清楚描述小诺需要独立完成什么，以及结果要求"
            placeholderTextColor={colors.muted}
            multiline
            autoFocus
            style={styles.goalInput}
            textAlignVertical="top"
          />
          <TaskLaunchEditor policy={launchPolicy} onChange={setLaunchPolicy} />
          <View style={styles.notificationCard}>
            <Text style={styles.launchTitle}>通知我</Text>
            <Toggle label="任务完成" value={notifyCompleted} onChange={setNotifyCompleted} />
            <Toggle label="任务失败" value={notifyFailed} onChange={setNotifyFailed} />
            <Toggle label="需要确认" value={notifyApproval} onChange={setNotifyApproval} />
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="创建并开始任务"
            disabled={!goal.trim() || saving || Boolean(gateway.requiredUpdate) || !isLaunchPolicyValid(launchPolicy)}
            onPress={() => void create()}
            style={[styles.primary, (!goal.trim() || saving || gateway.requiredUpdate || !isLaunchPolicyValid(launchPolicy)) && styles.disabled]}
          >
            {saving ? <ActivityIndicator color="white" /> : <Text style={styles.primaryText}>{launchPolicy.kind === "immediate" ? "创建并开始" : "创建任务"}</Text>}
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange(value: boolean): void }) {
  return <View style={styles.toggle}><Text style={styles.toggleLabel}>{label}</Text><Switch value={value} onValueChange={onChange} trackColor={{ true: colors.accentSoft }} thumbColor={value ? colors.accent : colors.line} /></View>;
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { padding: 16, paddingBottom: 48 },
  card: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 18, gap: 10 },
  updateRequired: { marginBottom: 12, padding: 14, borderRadius: 12, backgroundColor: colors.dangerSoft, gap: 4 },
  updateRequiredTitle: { color: colors.danger, fontWeight: "700" },
  label: { color: colors.ink, fontWeight: "700", marginTop: 4 },
  titleInput: { minHeight: 46, borderWidth: 1, borderColor: colors.line, borderRadius: 12, paddingHorizontal: 12, color: colors.ink, fontSize: 16 },
  goalInput: { minHeight: 180, borderWidth: 1, borderColor: colors.line, borderRadius: 12, padding: 12, color: colors.ink, fontSize: 16, lineHeight: 23 },
  launchCard: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.accentSoft, gap: 4 },
  launchTitle: { color: colors.ink, fontWeight: "700" },
  launchText: { color: colors.muted, lineHeight: 20 },
  notificationCard: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, gap: 8 },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  toggleLabel: { color: colors.ink },
  error: { color: colors.danger },
  primary: { marginTop: 8, minHeight: 48, borderRadius: 14, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  disabled: { opacity: 0.45 },
  primaryText: { color: "white", fontWeight: "700", fontSize: 16 },
});
