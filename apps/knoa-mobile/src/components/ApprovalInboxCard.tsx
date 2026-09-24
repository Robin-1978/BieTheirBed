import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import type { GatewayClient } from "@/api/gatewayClient";
import type { Task, TaskApproval, TaskExecution } from "@/api/models";
import { ApprovalCard } from "@/components/ApprovalCard";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, typography } from "@/theme";

type Props = {
  task: Task;
  /** status === "ready" from useConnection; load is gated on it. */
  ready: boolean;
  /** Stable runAuthenticated from useSession. */
  runAuthenticated: <T>(fn: (client: GatewayClient) => Promise<T>) => Promise<T>;
  /** Parent refresh after an approval is resolved. */
  onResolved: () => void;
  /** Jump to the legacy execution detail page. */
  onOpenExecution: (executionId: string) => void;
  /** Void the whole task (stop execution + archive). Shown for active tasks. */
  onVoid?: (task: Task) => void;
};

/**
 * Inline approval card for theWork page "needs_action" section.
 * Shows the pending tool calls with arguments and resolves them in place,
 * so approving no longer requires list -> detail -> execution jumps.
 */
export function ApprovalInboxCard({ task, ready, runAuthenticated, onResolved, onOpenExecution, onVoid }: Props) {
  const { t } = useI18n();
  const [execution, setExecution] = useState<TaskExecution | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [resolving, setResolving] = useState<{ id: string; approved: boolean } | null>(null);

  const executionId = task.latest_execution_id;

  const load = useCallback(async () => {
    if (!ready || !executionId) return;
    setLoadError(false);
    try {
      const snapshot = await runAuthenticated((client) => client.getTaskExecution(executionId));
      setExecution(snapshot);
    } catch {
      setLoadError(true);
    }
  }, [ready, executionId, runAuthenticated]);

  useEffect(() => {
    void load();
  }, [load]);

  const pending = (execution?.approvals ?? []).filter((item) => item.state === "pending");

  async function resolve(approval: TaskApproval, approved: boolean) {
    if (resolving) return;
    setResolving({ id: approval.approval_id, approved });
    try {
      await runAuthenticated((client) => client.resolveApproval(approval.approval_id, approved));
      setExecution((current) => current ? {
        ...current,
        approvals: current.approvals.map((item) => item.approval_id === approval.approval_id
          ? { ...item, state: approved ? "approved" : "denied" }
          : item),
      } : current);
      onResolved();
    } catch {
      setLoadError(true);
    } finally {
      setResolving(null);
    }
  }

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.title} numberOfLines={2}>{task.title}</Text>
        {onVoid ? (
          <AppPressable style={styles.voidButton} onPress={() => onVoid(task)}>
            <Text style={styles.voidText}>{t("tasks.voidTask")}</Text>
          </AppPressable>
        ) : null}
      </View>
      {pending.length === 0 && !loadError ? (
        <View style={styles.row}>
          <ActivityIndicator size="small" color={colors.accent} />
          <AppPressable style={styles.detailLink} onPress={() => onOpenExecution(executionId)}>
            <Text style={styles.detailText}>{t("tasks.bentoReview")}</Text>
          </AppPressable>
        </View>
      ) : null}
      {loadError ? (
        <AppPressable style={styles.detailLink} onPress={() => onOpenExecution(executionId)}>
          <Text style={styles.detailText}>{t("tasks.bentoReview")}</Text>
        </AppPressable>
      ) : null}
      {pending.map((approval, index) => (
        <ApprovalCard
          key={approval.approval_id}
          approval={approval}
          countLabel={pending.length > 1 ? `${index + 1}/${pending.length}` : undefined}
          resolvingId={resolving?.id}
          resolvingApproved={resolving?.approved ?? null}
          onApprove={(item) => void resolve(item, true)}
          onDeny={(item) => void resolve(item, false)}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.warning,
    padding: spacing.medium,
    gap: spacing.small,
  },
  title: { ...typography.subheading, color: colors.ink, flex: 1 },
  header: { flexDirection: "row", alignItems: "flex-start", gap: spacing.small },
  voidButton: { paddingVertical: spacing.xsmall, paddingHorizontal: spacing.small },
  voidText: { ...typography.caption, color: colors.danger, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.small },
  detailLink: { paddingVertical: spacing.xsmall },
  detailText: { ...typography.caption, color: colors.accent, fontWeight: "700" },
  approval: { gap: spacing.xsmall },
  count: { ...typography.tiny, color: colors.muted },
  reason: { ...typography.caption, color: colors.muted },
  actions: { flexDirection: "row", gap: spacing.small },
  button: {
    flex: 1,
    borderRadius: radii.medium,
    paddingVertical: spacing.small,
    alignItems: "center",
    justifyContent: "center",
  },
  deny: { borderWidth: 1, borderColor: colors.danger },
  denyText: { color: colors.danger, fontWeight: "700" },
  allow: { backgroundColor: colors.accent },
  allowText: { color: colors.onAccent, fontWeight: "700" },
});
