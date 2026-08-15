import { useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import type { ApprovalDisplay } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

export function ApprovalRequestDetails({
  toolName,
  arguments: toolArguments,
  display,
  showTitle = true,
}: {
  toolName: string;
  arguments: Record<string, unknown>;
  display?: ApprovalDisplay;
  showTitle?: boolean;
}) {
  const { t } = useI18n();
  const [technicalExpanded, setTechnicalExpanded] = useState(false);
  const argumentsPreview = display?.arguments_preview
    || (Object.keys(toolArguments).length ? JSON.stringify(toolArguments, null, 2) : "");
  const action = display?.action_summary || toolName;
  const reviewerDecision = display?.reviewer_decision || "";

  return (
    <View style={styles.container}>
      {showTitle ? <Text style={styles.title}>{t("execution.approvalTitle")}</Text> : null}
      <Text style={styles.action}>{action}</Text>
      {display?.target_summary ? (
        <View style={styles.fact}>
          <Text style={styles.factLabel}>{t("execution.approvalTarget")}</Text>
          <Text selectable style={styles.factValue}>{display.target_summary}</Text>
        </View>
      ) : null}
      <Text style={styles.explanation}>{manualReasonLabel(display?.manual_reason, t)}</Text>
      {display?.instruction_excerpt ? (
        <View style={styles.instruction}>
          <Text style={styles.factLabel}>{t("execution.approvalInstruction")}</Text>
          <Text style={styles.instructionText}>{display.instruction_excerpt}</Text>
        </View>
      ) : null}
      {reviewerDecision ? (
        <View style={[
          styles.review,
          reviewerDecision === "deny" && styles.reviewDanger,
          reviewerDecision === "escalate" && styles.reviewWarning,
        ]}>
          <Text style={styles.factLabel}>{t("execution.approvalReviewer")}</Text>
          <Text style={styles.reviewDecision}>{reviewerDecisionLabel(reviewerDecision, t)}</Text>
          {display?.reviewer_reason ? <Text style={styles.reviewReason}>{display.reviewer_reason}</Text> : null}
        </View>
      ) : null}
      <Text style={styles.policy}>
        {t("execution.effect", { value: effectLabel(display?.effect ?? "unknown", t) })}
        {" · "}
        {t("execution.risk", { value: riskLabel(display?.risk ?? "unknown", t) })}
        {" · "}
        {display?.reversible ? t("execution.reversible") : t("execution.irreversible")}
      </Text>
      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={technicalExpanded ? t("execution.hideTechnical") : t("execution.showTechnical")}
        onPress={() => setTechnicalExpanded((value) => !value)}
        style={styles.technicalToggle}
      >
        <Text style={styles.technicalToggleText}>{technicalExpanded ? t("execution.hideTechnical") : t("execution.showTechnical")}</Text>
        <AppIcon name={technicalExpanded ? "chevron-down" : "chevron-right"} color={colors.muted} size={16} />
      </AppPressable>
      {technicalExpanded ? (
        <View style={styles.technical}>
          <Text style={styles.factLabel}>{t("execution.tool")}</Text>
          <Text selectable style={styles.tool}>{toolName}</Text>
          <Text style={styles.factLabel}>{t("execution.arguments")}</Text>
          <Text selectable style={styles.arguments}>{argumentsPreview || t("execution.noArguments")}</Text>
        </View>
      ) : null}
    </View>
  );
}

function manualReasonLabel(value: ApprovalDisplay["manual_reason"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    high_risk: t("execution.approvalReason.highRisk"),
    reviewer_escalated: t("execution.approvalReason.reviewerEscalated"),
    reviewer_suggest_only: t("execution.approvalReason.reviewerSuggest"),
    policy_confirmation: t("execution.approvalReason.policy"),
  } as Record<string, string>)[value || "policy_confirmation"] ?? t("execution.approvalReason.policy");
}

function reviewerDecisionLabel(value: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    approve: t("execution.reviewer.approve"),
    deny: t("execution.reviewer.deny"),
    escalate: t("execution.reviewer.escalate"),
  } as Record<string, string>)[value] ?? t("execution.reviewer.escalate");
}

function effectLabel(value: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ read_only: t("execution.effect.readOnly"), internal_write: t("execution.effect.internal"), local_write: t("execution.effect.local"), external_side_effect: t("execution.effect.external"), desktop_control: t("execution.effect.desktop"), unknown: t("execution.effect.unknown") } as Record<string, string>)[value] ?? t("execution.effect.controlled");
}

function riskLabel(value: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ low: t("execution.risk.low"), medium: t("execution.risk.medium"), high: t("execution.risk.high") } as Record<string, string>)[value] ?? t("execution.risk.unknown");
}

const styles = StyleSheet.create({
  container: { gap: 9 },
  title: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  action: { color: colors.ink, fontSize: 17, lineHeight: 24, fontWeight: "700" },
  explanation: { color: colors.ink, lineHeight: 21 },
  fact: { gap: 3 },
  factLabel: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  factValue: { color: colors.ink, lineHeight: 20 },
  instruction: { padding: 11, borderRadius: 11, backgroundColor: colors.surface, gap: 4 },
  instructionText: { color: colors.ink, lineHeight: 21 },
  review: { padding: 11, borderRadius: 11, backgroundColor: colors.accentFaint, gap: 3 },
  reviewDanger: { backgroundColor: colors.dangerSoft },
  reviewWarning: { backgroundColor: colors.warningSoft },
  reviewDecision: { color: colors.ink, fontWeight: "700" },
  reviewReason: { color: colors.ink, lineHeight: 20 },
  policy: { color: colors.muted, fontSize: 13, lineHeight: 20 },
  technicalToggle: { minHeight: 40, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5 },
  technicalToggleText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  technical: { padding: 10, borderRadius: 10, backgroundColor: colors.surfaceMuted, gap: 5 },
  tool: { color: colors.accent, fontFamily: "monospace", fontSize: 12 },
  arguments: { color: colors.ink, fontFamily: "monospace", fontSize: 12, lineHeight: 18 },
});
