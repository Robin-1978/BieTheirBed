import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import type { ApprovalDisplay } from "@/api/models";
import { ApprovalRequestDetails } from "@/components/ApprovalRequestDetails";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, typography } from "@/theme";

export type ApprovalCardItem = {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason?: string;
  display?: ApprovalDisplay;
};

type Props<T extends ApprovalCardItem> = {
  approval: T;
  /** "1/2" style position label when several are pending. */
  countLabel?: string;
  /** Optional header (task title / turn label). */
  title?: string;
  /** Id of the approval currently resolving (disables both buttons). */
  resolvingId?: string;
  resolvingApproved?: boolean | null;
  onApprove: (approval: T) => void;
  onDeny: (approval: T) => void;
};

/**
 * The single approval UI for task executions, chat turns and the work
 * inbox: tool + human-readable arguments + reason + allow/deny.
 * Call sites keep their own resolve logic; this is presentational.
 */
export function ApprovalCard<T extends ApprovalCardItem>({
  approval,
  countLabel,
  title,
  resolvingId,
  resolvingApproved,
  onApprove,
  onDeny,
}: Props<T>) {
  const { t } = useI18n();
  const busy = Boolean(resolvingId);
  const resolvingThis = resolvingId === approval.approval_id;

  return (
    <View style={styles.card}>
      {title ? <Text style={styles.title} numberOfLines={2}>{title}</Text> : null}
      {countLabel ? <Text style={styles.count}>{countLabel}</Text> : null}
      <ApprovalRequestDetails
        toolName={approval.tool_name}
        arguments={approval.arguments}
        display={approval.display}
      />
      {approval.reason ? <Text style={styles.reason}>{approval.reason}</Text> : null}
      <View style={styles.actions}>
        <AppPressable
          style={[styles.button, styles.deny]}
          disabled={busy}
          onPress={() => onDeny(approval)}
        >
          {resolvingThis && resolvingApproved === false
            ? <ActivityIndicator size="small" color={colors.danger} />
            : <Text style={styles.denyText}>{t("execution.denyAction")}</Text>}
        </AppPressable>
        <AppPressable
          style={[styles.button, styles.allow]}
          disabled={busy}
          onPress={() => onApprove(approval)}
        >
          {resolvingThis && resolvingApproved === true
            ? <ActivityIndicator size="small" color={colors.onAccent} />
            : <Text style={styles.allowText}>{t("execution.allowAction")}</Text>}
        </AppPressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { gap: spacing.xsmall },
  title: { ...typography.subheading, color: colors.ink },
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
