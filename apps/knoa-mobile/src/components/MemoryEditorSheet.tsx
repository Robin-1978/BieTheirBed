import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { GatewayClient } from "@/api/gatewayClient";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors, radii, shadows, spacing } from "@/theme";

export type MemoryDraft = {
  key: string;
  value: string;
  category: string;
  importance: "core" | "relevant";
};

const CATEGORIES = [
  { key: "preference", labelKey: "memories.catPreference" },
  { key: "instruction", labelKey: "memories.catInstruction" },
  { key: "workflow", labelKey: "memories.catWorkflow" },
  { key: "identity", labelKey: "memories.catIdentity" },
  { key: "environment", labelKey: "memories.catEnvironment" },
  { key: "safety", labelKey: "memories.catSafety" },
  { key: "general", labelKey: "memories.catGeneral" },
] as const;

export function MemoryEditorSheet({
  visible,
  editingKey,
  initial,
  hasClient,
  runAuthenticated,
  onClose,
  onSaved,
}: {
  visible: boolean;
  editingKey: string | null;
  initial: MemoryDraft;
  hasClient: boolean;
  runAuthenticated: <T>(operation: (client: GatewayClient) => Promise<T>) => Promise<T>;
  onClose(): void;
  onSaved(): void;
}) {
  const { t } = useI18n();
  const [formKey, setFormKey] = useState(initial.key);
  const [formValue, setFormValue] = useState(initial.value);
  const [formCategory, setFormCategory] = useState(initial.category);
  const [formImportance, setFormImportance] = useState<"core" | "relevant">(initial.importance);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!visible) return;
    setFormKey(initial.key);
    setFormValue(initial.value);
    setFormCategory(initial.category);
    setFormImportance(initial.importance);
    setFormError("");
  }, [visible, editingKey, initial]);

  async function save() {
    const trimmedKey = formKey.trim().toLowerCase();
    const trimmedVal = formValue.trim();
    if (!trimmedKey) {
      setFormError("键名不能为空");
      return;
    }
    if (!trimmedVal) {
      setFormError("记忆内容不能为空");
      return;
    }
    if (!editingKey && !/^[a-z][a-z0-9_]{1,63}$/.test(trimmedKey)) {
      setFormError("键名须以小写字母开头，仅含英文字母、数字或下划线 (2-64位)");
      return;
    }
    if (!hasClient) return;
    setSaving(true);
    setFormError("");
    try {
      if (editingKey) {
        await runAuthenticated((client) =>
          client.updateMemory(editingKey, {
            value: trimmedVal,
            category: formCategory,
            importance: formImportance,
          }),
        );
      } else {
        await runAuthenticated((client) =>
          client.createMemory({
            key: trimmedKey,
            value: trimmedVal,
            category: formCategory,
            importance: formImportance,
          }),
        );
      }
      onSaved();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      transparent
      animationType="slide"
      visible={visible}
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.modalBackdrop}
      >
        <AppPressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.editorSheet}>
          <View style={styles.sheetHeader}>
            <View style={styles.sheetHeaderTitleWrap}>
              <AppIcon name="edit" color={colors.accent} size={16} />
              <Text style={styles.sheetTitle}>
                {editingKey ? t("memories.editMemory") : t("memories.addMemory")}
              </Text>
            </View>
            <AppPressable style={styles.sheetCloseBtn} onPress={onClose}>
              <AppIcon name="x" color={colors.muted} size={18} />
            </AppPressable>
          </View>

          <ScrollView
            style={styles.sheetScroll}
            contentContainerStyle={styles.sheetContent}
            keyboardShouldPersistTaps="handled"
          >
            <View style={styles.formGroup}>
              <Text style={styles.formLabel}>{t("memories.key")}</Text>
              <TextInput
                editable={!editingKey}
                value={formKey}
                onChangeText={(val) => {
                  setFormKey(val);
                  if (formError) setFormError("");
                }}
                placeholder={t("memories.keyPlaceholder")}
                placeholderTextColor={colors.muted}
                autoCapitalize="none"
                style={[styles.textInput, !!editingKey && styles.textInputDisabled]}
              />
            </View>

            <View style={styles.formGroup}>
              <Text style={styles.formLabel}>{t("memories.importanceLabel")}</Text>
              <View style={styles.segmentRow}>
                <AppPressable
                  style={[styles.segmentBtn, formImportance === "core" && styles.segmentBtnActive]}
                  onPress={() => setFormImportance("core")}
                >
                  <Text style={[styles.segmentBtnText, formImportance === "core" && styles.segmentBtnTextActive]}>
                    {t("memories.filterCore")}
                  </Text>
                </AppPressable>
                <AppPressable
                  style={[styles.segmentBtn, formImportance === "relevant" && styles.segmentBtnActive]}
                  onPress={() => setFormImportance("relevant")}
                >
                  <Text style={[styles.segmentBtnText, formImportance === "relevant" && styles.segmentBtnTextActive]}>
                    {t("memories.filterRelevant")}
                  </Text>
                </AppPressable>
              </View>
            </View>

            <View style={styles.formGroup}>
              <Text style={styles.formLabel}>{t("memories.categoryLabel")}</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.catChipsScroll}>
                {CATEGORIES.map((cat) => (
                  <AppPressable
                    key={cat.key}
                    style={[styles.catChip, formCategory === cat.key && styles.catChipActive]}
                    onPress={() => setFormCategory(cat.key)}
                  >
                    <Text style={[styles.catChipText, formCategory === cat.key && styles.catChipTextActive]}>
                      {t(cat.labelKey)}
                    </Text>
                  </AppPressable>
                ))}
              </ScrollView>
            </View>

            <View style={styles.formGroup}>
              <Text style={styles.formLabel}>{t("memories.value")}</Text>
              <TextInput
                value={formValue}
                onChangeText={(val) => {
                  setFormValue(val);
                  if (formError) setFormError("");
                }}
                placeholder={t("memories.valuePlaceholder")}
                placeholderTextColor={colors.muted}
                multiline
                numberOfLines={4}
                style={[styles.textInput, styles.textArea]}
              />
            </View>

            {formError ? <Text style={styles.formErrorText}>{formError}</Text> : null}
          </ScrollView>

          <View style={styles.sheetFooter}>
            <AppPressable
              disabled={saving}
              style={styles.sheetCancelBtn}
              onPress={onClose}
            >
              <Text style={styles.sheetCancelBtnText}>{t("memories.cancel")}</Text>
            </AppPressable>

            <AppPressable
              disabled={saving}
              style={[styles.sheetSaveBtn, saving && styles.sheetSaveBtnDisabled]}
              onPress={() => void save()}
            >
              {saving ? (
                <ActivityIndicator color={colors.onAccent} size="small" />
              ) : (
                <>
                  <AppIcon name="save" color={colors.onAccent} size={14} />
                  <Text style={styles.sheetSaveBtnText}>{t("memories.save")}</Text>
                </>
              )}
            </AppPressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalBackdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.45)",
  },
  editorSheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radii.large,
    borderTopRightRadius: radii.large,
    maxHeight: "85%",
    paddingBottom: 24,
    ...shadows.floating,
  },
  sheetHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.large,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  sheetHeaderTitleWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  sheetTitle: {
    color: colors.ink,
    fontSize: 16,
    fontWeight: "700",
  },
  sheetCloseBtn: {
    padding: 4,
    borderRadius: radii.small,
  },
  sheetScroll: {
    maxHeight: 460,
  },
  sheetContent: {
    padding: spacing.large,
    gap: spacing.large,
  },
  formGroup: {
    gap: 8,
  },
  formLabel: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "600",
  },
  textInput: {
    backgroundColor: colors.surfaceMuted,
    borderRadius: radii.medium,
    paddingHorizontal: spacing.medium,
    paddingVertical: 10,
    fontSize: 15,
    color: colors.ink,
    borderWidth: 1,
    borderColor: colors.line,
  },
  textInputDisabled: {
    color: colors.muted,
    backgroundColor: colors.line,
  },
  textArea: {
    minHeight: 90,
    textAlignVertical: "top",
  },
  segmentRow: {
    flexDirection: "row",
    gap: spacing.small,
  },
  segmentBtn: {
    flex: 1,
    paddingVertical: 9,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surfaceMuted,
    alignItems: "center",
  },
  segmentBtnActive: {
    backgroundColor: colors.accentSoft,
    borderColor: colors.accent,
  },
  segmentBtnText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600",
  },
  segmentBtnTextActive: {
    color: colors.accent,
    fontWeight: "700",
  },
  catChipsScroll: {
    gap: spacing.small,
    paddingVertical: 2,
  },
  catChip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: radii.pill,
    backgroundColor: colors.surfaceMuted,
    borderWidth: 1,
    borderColor: colors.line,
  },
  catChipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  catChipText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
  },
  catChipTextActive: {
    color: colors.onAccent,
    fontWeight: "700",
  },
  formErrorText: {
    color: colors.danger,
    fontSize: 12,
    lineHeight: 16,
  },
  sheetFooter: {
    flexDirection: "row",
    paddingHorizontal: spacing.large,
    paddingTop: spacing.medium,
    gap: spacing.medium,
  },
  sheetCancelBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    alignItems: "center",
    justifyContent: "center",
  },
  sheetCancelBtnText: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "600",
  },
  sheetSaveBtn: {
    flex: 2,
    flexDirection: "row",
    gap: 6,
    paddingVertical: 12,
    borderRadius: radii.medium,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  sheetSaveBtnDisabled: {
    opacity: 0.6,
  },
  sheetSaveBtnText: {
    color: colors.onAccent,
    fontSize: 15,
    fontWeight: "700",
  },
});
