import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import type { ChatApproval } from "@/api/models";
import { AppPressable } from "@/components/AppPressable";
import { ApprovalRequestDetails } from "@/components/ApprovalRequestDetails";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, typography } from "@/theme";

export type ChatApprovalCardProps = {
  approval: ChatApproval;
  resolving: string;
  resolvingApproved: boolean | null;
  onResolve(approval: ChatApproval, approved: boolean): void;
};

export function ChatApprovalCard({
  approval,
  resolving,
  resolvingApproved,
  onResolve,
}: ChatApprovalCardProps) {
  const { t } = useI18n();
  const isResolving = resolving === approval.approval_id;

  return (
    <View style={styles.approval}>
      <ApprovalRequestDetails
        toolName={approval.tool_name}
        arguments={approval.arguments}
        display={approval.display}
      />
      <View style={styles.approvalActions}>
        <AppPressable
          style={styles.deny}
          disabled={Boolean(resolving)}
          onPress={() => onResolve(approval, false)}
        >
          {isResolving && resolvingApproved === false ? (
            <ActivityIndicator color={colors.ink} size="small" />
          ) : (
            <Text style={styles.denyText}>{t("execution.denyAction")}</Text>
          )}
        </AppPressable>
        <AppPressable
          style={styles.approve}
          disabled={Boolean(resolving)}
          onPress={() => onResolve(approval, true)}
        >
          {isResolving && resolvingApproved === true ? (
            <ActivityIndicator color={colors.onAccent} size="small" />
          ) : (
            <Text style={styles.approveText}>{t("execution.allowAction")}</Text>
          )}
        </AppPressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  approval: {
    borderRadius: radii.medium,
    backgroundColor: colors.dangerSoft,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.medium,
    gap: spacing.medium,
    marginVertical: spacing.xsmall,
  },
  approvalActions: {
    flexDirection: "row",
    gap: spacing.small,
  },
  deny: {
    flex: 1,
    minHeight: 38,
    borderRadius: radii.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.medium,
  },
  denyText: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "700",
  },
  approve: {
    flex: 1,
    minHeight: 38,
    borderRadius: radii.medium,
    backgroundColor: colors.danger,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.medium,
  },
  approveText: {
    color: colors.onAccent,
    fontSize: 13,
    fontWeight: "800",
  },
});
