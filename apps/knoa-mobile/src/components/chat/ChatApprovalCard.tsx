import { StyleSheet, View } from "react-native";

import type { ChatApproval } from "@/api/models";
import { ApprovalCard } from "@/components/ApprovalCard";
import { colors, radii, spacing } from "@/theme";

export type ChatApprovalCardProps = {
  approval: ChatApproval;
  resolving: string;
  resolvingApproved: boolean | null;
  onResolve(approval: ChatApproval, approved: boolean): void;
};

/**
 * Chat-turn approval, rendered through the shared ApprovalCard so all
 * three approval surfaces stay identical.
 */
export function ChatApprovalCard({
  approval,
  resolving,
  resolvingApproved,
  onResolve,
}: ChatApprovalCardProps) {
  return (
    <View style={styles.approval}>
      <ApprovalCard
        approval={approval}
        resolvingId={resolving || undefined}
        resolvingApproved={resolvingApproved}
        onApprove={(item) => onResolve(item, true)}
        onDeny={(item) => onResolve(item, false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  approval: {
    backgroundColor: colors.surface,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.warning,
    padding: spacing.medium,
  },
});
