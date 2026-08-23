import { StyleSheet, Text, View } from "react-native";

import type { TaskExecution } from "@/api/models";
import { AppMarkdown } from "@/components/AppMarkdown";
import { structuredWorkChanges } from "@/components/workResultSummaryPresentation";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

export function WorkResultSummary({ execution }: { execution: TaskExecution }) {
  const { t } = useI18n();
  const changes = structuredWorkChanges(execution);
  if (!execution.final_result && !execution.failure_code && !changes.length) return null;
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{t("execution.result")}</Text>
      {execution.final_result ? <AppMarkdown value={execution.final_result} style={styles.markdown} /> : null}
      {execution.failure_code ? (
        <View style={styles.incomplete}>
          <Text style={styles.incompleteTitle}>{t("execution.incomplete")}</Text>
          <Text style={styles.detail}>{t("execution.incompleteHelp")}</Text>
          {execution.work_status?.side_effect === "unknown" ? <Text style={styles.impact}>{t("execution.sideEffectUnknown")}</Text> : null}
          {execution.work_status?.side_effect === "possible" ? <Text style={styles.impact}>{t("execution.sideEffectPossible")}</Text> : null}
        </View>
      ) : null}
      {changes.length ? (
        <View style={styles.changes}>
          <Text style={styles.subtitle}>{t("execution.structuredChanges")}</Text>
          {changes.map((item, index) => (
            <Text key={`${item.reference}:${index}`} selectable style={styles.change}>• {item.label} · {item.reference}</Text>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { padding: 16, borderRadius: 16, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, gap: 10 },
  title: { color: colors.ink, fontSize: 17, fontWeight: "800" },
  subtitle: { color: colors.ink, fontWeight: "800" },
  markdown: { width: "100%", alignSelf: "stretch" },
  incomplete: { padding: 12, borderRadius: 12, backgroundColor: colors.dangerSoft, gap: 4 },
  incompleteTitle: { color: colors.danger, fontWeight: "800" },
  detail: { color: colors.muted, lineHeight: 20 },
  impact: { color: colors.danger, lineHeight: 20 },
  changes: { gap: 5 },
  change: { color: colors.muted, lineHeight: 19 },
});
