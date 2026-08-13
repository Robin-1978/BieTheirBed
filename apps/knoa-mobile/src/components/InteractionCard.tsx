import { useMemo, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, TextInput, View } from "react-native";

import type { HumanInteraction } from "@/api/models";
import { AppPressable } from "@/components/AppPressable";
import { colors } from "@/theme";

export function InteractionCard({
  interaction,
  submitting,
  onSubmit,
}: {
  interaction: HumanInteraction;
  submitting: boolean;
  onSubmit(value: Record<string, unknown>): void;
}) {
  const fields = interaction.display.fields ?? [];
  const properties = interaction.resolution_schema.properties ?? {};
  const required = useMemo(
    () => new Set(interaction.resolution_schema.required ?? []),
    [interaction.resolution_schema.required],
  );
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const supported = interaction.resolution_schema.type === "object"
    && interaction.resolution_schema.additionalProperties === false
    && fields.every((field) => {
      const schema = properties[field.id];
      return schema?.type === "string" || schema?.type === "boolean";
    });
  const complete = supported && fields.every((field) => {
    if (!required.has(field.id)) return true;
    const value = values[field.id];
    return typeof value === "string" ? value.trim().length > 0 : typeof value === "boolean";
  });

  return (
    <View style={styles.card}>
      <Text style={styles.eyebrow}>需要你的输入 · 这不是写入授权</Text>
      <Text style={styles.title}>{interaction.display.title || "补充信息"}</Text>
      {interaction.display.description ? <Text style={styles.description}>{interaction.display.description}</Text> : null}
      {!supported ? <Text style={styles.unsupported}>当前版本暂不支持这个表单，请在其他客户端处理。</Text> : fields.map((field) => {
        const schema = properties[field.id] ?? {};
        const options: Array<{ value: string; label: string; description?: string }> = field.options
          ?? (schema.enum ?? []).map((value) => ({ value, label: value }));
        const textValue = values[field.id];
        return (
          <View key={field.id} style={styles.field}>
            <Text style={styles.label}>{field.title || schema.title || field.id}</Text>
            {field.description ? <Text style={styles.description}>{field.description}</Text> : null}
            {schema.type === "boolean" ? (
              <View style={styles.options}>
                {[true, false].map((value) => (
                  <Option key={String(value)} selected={values[field.id] === value} label={value ? "是" : "否"} onPress={() => setValues((current) => ({ ...current, [field.id]: value }))} />
                ))}
              </View>
            ) : options.length ? (
              <View style={styles.options}>
                {options.map((option) => (
                  <Option key={option.value} selected={values[field.id] === option.value} label={option.label} description={option.description} onPress={() => setValues((current) => ({ ...current, [field.id]: option.value }))} />
                ))}
              </View>
            ) : (
              <TextInput
                multiline={(schema.maxLength ?? 0) > 200}
                value={typeof textValue === "string" ? textValue : ""}
                onChangeText={(value) => setValues((current) => ({ ...current, [field.id]: value }))}
                maxLength={schema.maxLength ?? 4000}
                style={styles.input}
              />
            )}
          </View>
        );
      })}
      {supported ? (
        <AppPressable disabled={!complete || submitting} onPress={() => onSubmit(values)} style={[styles.submit, (!complete || submitting) && styles.disabled]}>
          {submitting ? <ActivityIndicator color="white" size="small" /> : <Text style={styles.submitText}>提交选择</Text>}
        </AppPressable>
      ) : null}
    </View>
  );
}

function Option({ selected, label, description, onPress }: { selected: boolean; label: string; description?: string; onPress(): void }) {
  return (
    <AppPressable onPress={onPress} style={[styles.option, selected && styles.optionSelected]}>
      <Text style={[styles.optionLabel, selected && styles.optionLabelSelected]}>{label}</Text>
      {description ? <Text style={styles.description}>{description}</Text> : null}
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: 12, padding: 14, borderRadius: 16, borderWidth: 1, borderColor: colors.accent, backgroundColor: colors.surface, gap: 10 },
  eyebrow: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  title: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  description: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  unsupported: { color: colors.danger, fontSize: 14 },
  field: { gap: 8 },
  label: { color: colors.ink, fontSize: 14, fontWeight: "600" },
  options: { gap: 8 },
  option: { padding: 12, borderRadius: 12, borderWidth: 1, borderColor: colors.line, gap: 3 },
  optionSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  optionLabel: { color: colors.ink, fontSize: 14 },
  optionLabelSelected: { color: colors.accent, fontWeight: "700" },
  input: { minHeight: 44, padding: 10, borderRadius: 10, borderWidth: 1, borderColor: colors.line, color: colors.ink, textAlignVertical: "top" },
  submit: { minHeight: 44, borderRadius: 12, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  disabled: { opacity: 0.45 },
  submitText: { color: "white", fontWeight: "700" },
});
