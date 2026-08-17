import { router } from "expo-router";
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

export default function NewTaskScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
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
  const requestIdentity = useRef<{ fingerprint: string; requestId: string } | null>(null);

  useEffect(() => {
    if (!gateway.client) return;
    void gateway.runAuthenticated((client) => client.listMcpResources())
      .then(setMcpResources)
      .catch(() => setMcpResources([]));
  }, [gateway.client, gateway.runAuthenticated]);

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
    } catch {
      setError(t("taskNew.createFailed"));
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
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <AppPressable
            accessibilityRole="button"
            accessibilityLabel={launchPolicy.kind === "immediate" ? t("taskNew.createAndStart") : t("taskNew.create")}
            disabled={!goal.trim() || saving || Boolean(gateway.requiredUpdate) || !isLaunchPolicyValid(launchPolicy)}
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
