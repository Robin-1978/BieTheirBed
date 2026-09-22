import { ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { TASK_TEMPLATES, type TaskTemplate } from "@/taskTemplates";
import { colors, radii, spacing, shadows } from "@/theme";

export function TemplatePickerCard({
  selectedTemplate,
  onSelect,
}: {
  selectedTemplate: string;
  onSelect(template: TaskTemplate): void;
}) {
  const { t } = useI18n();
  const activeTemplate = TASK_TEMPLATES.find((template) => template.id === selectedTemplate);
  return (
    <View style={styles.card}>
      <View style={styles.sectionHeader}>
        <AppIcon name="agent" color={colors.accent} size={18} />
        <Text style={styles.sectionTitle}>{t("taskTemplates.title")}</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.templateRow}>
        {TASK_TEMPLATES.map((template) => {
          const isSelected = selectedTemplate === template.id;
          return (
            <AppPressable
              key={template.id}
              style={[styles.template, isSelected && styles.templateSelected]}
              onPress={() => onSelect(template)}
            >
              <Text style={[styles.templateTitle, isSelected && styles.templateSelectedText]}>
                {t(template.titleKey)}
              </Text>
              <Text style={styles.templateDetail} numberOfLines={2}>
                {t(template.detailKey)}
              </Text>
            </AppPressable>
          );
        })}
      </ScrollView>

      {activeTemplate ? (
        <View style={styles.templateDetails}>
          <Text style={styles.templateDetailsTitle}>{t(activeTemplate.titleKey)}</Text>
          <View style={styles.chipsRow}>
            <View style={styles.metaChip}><Text style={styles.metaChipText}>{t(activeTemplate.durationKey)}</Text></View>
            <View style={styles.metaChip}><Text style={styles.metaChipText}>{t(activeTemplate.connectionKey)}</Text></View>
          </View>
          <Text style={styles.templateMeta}>{t("taskTemplates.result", { value: t(activeTemplate.resultKey) })}</Text>
          <Text style={styles.templateMeta}>{t("taskTemplates.permission", { value: t(activeTemplate.permissionKey) })}</Text>
          <Text style={styles.templateMeta}>{t("taskTemplates.failure", { value: t(activeTemplate.failureKey) })}</Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.large,
    gap: spacing.medium,
    ...shadows.card,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  sectionTitle: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "700",
  },
  templateRow: {
    gap: spacing.small,
    paddingVertical: 4,
  },
  template: {
    width: 160,
    minHeight: 82,
    padding: spacing.medium,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.background,
    gap: 4,
  },
  templateSelected: {
    borderColor: colors.accent,
    backgroundColor: colors.accentFaint,
  },
  templateTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "700",
  },
  templateSelectedText: {
    color: colors.accent,
  },
  templateDetail: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 15,
  },
  templateDetails: {
    padding: spacing.medium,
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    gap: spacing.xsmall,
  },
  templateDetailsTitle: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "700",
  },
  chipsRow: {
    flexDirection: "row",
    gap: spacing.small,
    marginVertical: 4,
  },
  metaChip: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: radii.small,
    backgroundColor: colors.accentSoft,
  },
  metaChipText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
  },
  templateMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16,
  },
});
