import React from "react";
import { StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { colors, radii, spacing, typography } from "@/theme";
import type { ActionCardInput } from "./types";

export type CardFormRendererProps = {
  inputs: ActionCardInput[];
  values: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
  disabled?: boolean;
};

export function CardFormRenderer({ inputs, values, onChange, disabled }: CardFormRendererProps) {
  if (!inputs || inputs.length === 0) return null;

  return (
    <View style={styles.container}>
      {inputs.map((input) => (
        <View key={input.id} style={styles.fieldContainer}>
          <View style={styles.labelRow}>
            <Text style={styles.label}>{input.label}</Text>
            {input.required ? <Text style={styles.requiredMark}>*</Text> : null}
          </View>

          {input.input_type === "textarea" ? (
            <TextInput
              style={[styles.input, styles.textarea, disabled && styles.disabledInput]}
              placeholder={input.placeholder}
              placeholderTextColor={colors.muted}
              multiline
              numberOfLines={4}
              value={String(values[input.id] ?? input.default_value ?? "")}
              onChangeText={(text) => onChange(input.id, text)}
              editable={!disabled}
            />
          ) : input.input_type === "switch" ? (
            <View style={styles.switchRow}>
              <Switch
                value={Boolean(values[input.id] ?? input.default_value ?? false)}
                onValueChange={(val) => onChange(input.id, val)}
                disabled={disabled}
                trackColor={{ false: colors.line, true: colors.accent }}
              />
            </View>
          ) : input.input_type === "select" ? (
            <View style={styles.optionsWrap}>
              {(input.options ?? []).map((opt) => {
                const isSelected = (values[input.id] ?? input.default_value) === opt.value;
                return (
                  <AppPressable
                    key={opt.value}
                    style={[
                      styles.optionChip,
                      isSelected && styles.optionChipSelected,
                      disabled && styles.disabledOption,
                    ]}
                    disabled={disabled}
                    onPress={() => onChange(input.id, opt.value)}
                  >
                    <Text
                      style={[
                        styles.optionText,
                        isSelected && styles.optionTextSelected,
                      ]}
                    >
                      {opt.label}
                    </Text>
                  </AppPressable>
                );
              })}
            </View>
          ) : (
            <TextInput
              style={[styles.input, disabled && styles.disabledInput]}
              placeholder={input.placeholder}
              placeholderTextColor={colors.muted}
              value={String(values[input.id] ?? input.default_value ?? "")}
              onChangeText={(text) => onChange(input.id, text)}
              editable={!disabled}
            />
          )}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: spacing.medium,
    marginVertical: spacing.small,
  },
  fieldContainer: {
    gap: spacing.xsmall,
  },
  labelRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  label: {
    fontSize: typography.caption.fontSize,
    fontWeight: "700",
    color: colors.ink,
  },
  requiredMark: {
    color: colors.danger,
    fontSize: typography.caption.fontSize,
  },
  input: {
    backgroundColor: colors.surfaceElevated,
    borderRadius: radii.small,
    borderWidth: 1,
    borderColor: colors.line,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    fontSize: typography.body.fontSize,
    color: colors.ink,
  },
  textarea: {
    minHeight: 80,
    textAlignVertical: "top",
  },
  disabledInput: {
    backgroundColor: colors.surfaceMuted,
    color: colors.muted,
  },
  switchRow: {
    alignItems: "flex-start",
  },
  optionsWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.small,
  },
  optionChip: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderWidth: 1,
    borderColor: colors.line,
  },
  optionChipSelected: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  disabledOption: {
    opacity: 0.6,
  },
  optionText: {
    fontSize: typography.caption.fontSize,
    color: colors.ink,
    fontWeight: "600",
  },
  optionTextSelected: {
    color: colors.onAccent,
  },
});
