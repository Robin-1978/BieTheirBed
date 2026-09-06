import { Stack } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { MemoryRecord } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  categoryDisplayName,
  filterMemories,
  formatConfidencePercent,
  type MemoryFilter,
} from "@/components/memoryPresentation";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, shadows, spacing, typography } from "@/theme";

const CATEGORIES = [
  { key: "preference", labelKey: "memories.catPreference" },
  { key: "instruction", labelKey: "memories.catInstruction" },
  { key: "workflow", labelKey: "memories.catWorkflow" },
  { key: "identity", labelKey: "memories.catIdentity" },
  { key: "environment", labelKey: "memories.catEnvironment" },
  { key: "safety", labelKey: "memories.catSafety" },
  { key: "general", labelKey: "memories.catGeneral" },
] as const;

export default function MemoriesScreen() {
  const { t } = useI18n();
  const gateway = useGateway();

  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [filter, setFilter] = useState<MemoryFilter>("all");

  // 编辑/新增弹窗状态
  const [editorVisible, setEditorVisible] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [formKey, setFormKey] = useState("");
  const [formValue, setFormValue] = useState("");
  const [formCategory, setFormCategory] = useState("preference");
  const [formImportance, setFormImportance] = useState<"core" | "relevant">("core");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const load = useCallback(async () => {
    if (!gateway.client || gateway.status !== "ready") {
      setLoading(false);
      return;
    }
    setRefreshing(true);
    try {
      const response = await gateway.runAuthenticated((client) => client.listMemories());
      setMemories(response.items || []);
    } catch {
      // ignore load errors
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [gateway.client, gateway.runAuthenticated, gateway.status]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleOpenCreate = useCallback(() => {
    setEditingKey(null);
    setFormKey("");
    setFormValue("");
    setFormCategory("preference");
    setFormImportance("core");
    setFormError("");
    setEditorVisible(true);
  }, []);

  const handleOpenEdit = useCallback((item: MemoryRecord) => {
    setEditingKey(item.key);
    setFormKey(item.key);
    setFormValue(item.value);
    setFormCategory(item.category);
    setFormImportance(item.importance);
    setFormError("");
    setEditorVisible(true);
  }, []);

  const handleSaveMemory = useCallback(async () => {
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

    if (!gateway.client) return;
    setSaving(true);
    setFormError("");

    try {
      if (editingKey) {
        await gateway.runAuthenticated((client) =>
          client.updateMemory(editingKey, {
            value: trimmedVal,
            category: formCategory,
            importance: formImportance,
          }),
        );
      } else {
        await gateway.runAuthenticated((client) =>
          client.createMemory({
            key: trimmedKey,
            value: trimmedVal,
            category: formCategory,
            importance: formImportance,
          }),
        );
      }
      setEditorVisible(false);
      void load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "保存失败，请稍后重试");
    } finally {
      setSaving(false);
    }
  }, [editingKey, formCategory, formImportance, formKey, formValue, gateway.client, gateway.runAuthenticated, load]);

  const handleClearAll = useCallback(() => {
    Alert.alert(
      t("memories.clearAll"),
      t("memories.clearConfirm"),
      [
        { text: t("common.cancel"), style: "cancel" },
        {
          text: t("common.confirm"),
          style: "destructive",
          onPress: async () => {
            if (!gateway.client || clearing) return;
            setClearing(true);
            try {
              await gateway.runAuthenticated((client) => client.clearMemories());
              setMemories([]);
              Alert.alert("", t("memories.cleared"));
            } catch {
              // ignore
            } finally {
              setClearing(false);
            }
          },
        },
      ],
    );
  }, [clearing, gateway.client, gateway.runAuthenticated, t]);

  const handleDeleteMemory = useCallback((key: string) => {
    Alert.alert(
      t("memories.delete"),
      t("memories.deleteConfirm", { key }),
      [
        { text: t("common.cancel"), style: "cancel" },
        {
          text: t("common.confirm"),
          style: "destructive",
          onPress: async () => {
            if (!gateway.client) return;
            try {
              await gateway.runAuthenticated((client) => client.deleteMemory(key));
              setMemories((prev) => prev.filter((m) => m.key !== key));
            } catch {
              // ignore
            }
          },
        },
      ],
    );
  }, [gateway.client, gateway.runAuthenticated, t]);

  const filteredItems = useMemo(
    () => filterMemories(memories, filter),
    [filter, memories],
  );

  const filters: Array<{ label: string; value: MemoryFilter; count: number }> = useMemo(() => [
    { label: t("memories.filterAll"), value: "all", count: memories.length },
    { label: t("memories.filterCore"), value: "core", count: memories.filter((m) => m.importance === "core").length },
    { label: t("memories.filterRelevant"), value: "relevant", count: memories.filter((m) => m.importance === "relevant").length },
  ], [memories, t]);

  return (
    <View style={styles.container}>
      <Stack.Screen
        options={{
          title: t("memories.title"),
          headerRight: () => (
            <View style={styles.headerRightActions}>
              <AppPressable
                onPress={handleOpenCreate}
                style={styles.addHeaderBtn}
              >
                <AppIcon name="plus" color={colors.accent} size={15} />
                <Text style={styles.addHeaderBtnText}>{t("memories.addMemory")}</Text>
              </AppPressable>
              {memories.length > 0 ? (
                <AppPressable
                  disabled={clearing}
                  onPress={handleClearAll}
                  style={styles.clearHeaderBtn}
                >
                  {clearing ? (
                    <ActivityIndicator color={colors.danger} size="small" />
                  ) : (
                    <AppIcon name="trash" color={colors.muted} size={16} />
                  )}
                </AppPressable>
              ) : null}
            </View>
          ),
        }}
      />

      {/* 说明横幅 */}
      <View style={styles.heroBanner}>
        <View style={styles.heroIconWrap}>
          <AppIcon name="history" color={colors.accent} size={20} />
        </View>
        <View style={styles.heroTextWrap}>
          <Text style={styles.heroTitle}>{t("memories.title")}</Text>
          <Text style={styles.heroSubtitle}>{t("memories.subtitle")}</Text>
        </View>
        <AppPressable style={styles.heroAddBtn} onPress={handleOpenCreate}>
          <AppIcon name="plus" color={colors.onAccent} size={13} />
          <Text style={styles.heroAddBtnText}>{t("memories.addMemory")}</Text>
        </AppPressable>
      </View>

      {/* 过滤胶囊条 */}
      <View style={styles.filtersBar}>
        {filters.map((f) => {
          const isActive = filter === f.value;
          return (
            <AppPressable
              key={f.value}
              accessibilityRole="button"
              accessibilityState={{ selected: isActive }}
              style={[styles.filterChip, isActive && styles.filterChipActive]}
              onPress={() => setFilter(f.value)}
            >
              <Text style={[styles.filterChipText, isActive && styles.filterChipTextActive]}>
                {f.label} ({f.count})
              </Text>
            </AppPressable>
          );
        })}
      </View>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : (
        <FlatList
          data={filteredItems}
          keyExtractor={(item) => item.key}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
          contentContainerStyle={styles.list}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <View style={styles.emptyIconWrap}>
                <AppIcon name="history" color={colors.muted} size={36} />
              </View>
              <Text style={styles.emptyTitle}>{t("memories.empty")}</Text>
              <Text style={styles.emptyBody}>{t("memories.emptyBody")}</Text>
              <AppPressable style={styles.emptyAddBtn} onPress={handleOpenCreate}>
                <AppIcon name="plus" color={colors.onAccent} size={14} />
                <Text style={styles.emptyAddBtnText}>{t("memories.addMemory")}</Text>
              </AppPressable>
            </View>
          }
          renderItem={({ item }) => (
            <View style={styles.card}>
              {/* 卡片顶部类别与重要度 */}
              <View style={styles.cardHeader}>
                <View style={styles.cardHeaderLeft}>
                  <View style={styles.categoryBadge}>
                    <Text style={styles.categoryBadgeText}>
                      {categoryDisplayName(item.category)}
                    </Text>
                  </View>
                  <View
                    style={[
                      styles.importanceBadge,
                      item.importance === "core" ? styles.importanceCore : styles.importanceRelevant,
                    ]}
                  >
                    <Text
                      style={[
                        styles.importanceText,
                        item.importance === "core" ? styles.importanceCoreText : styles.importanceRelevantText,
                      ]}
                    >
                      {item.importance === "core" ? t("memories.filterCore") : t("memories.filterRelevant")}
                    </Text>
                  </View>
                </View>

                <View style={styles.cardHeaderRight}>
                  <View style={styles.confidencePill}>
                    <Text style={styles.confidenceLabel}>{t("memories.confidence")}</Text>
                    <Text style={styles.confidenceValue}>
                      {formatConfidencePercent(item.confidence)}
                    </Text>
                  </View>
                  <AppPressable
                    style={styles.editItemBtn}
                    onPress={() => handleOpenEdit(item)}
                  >
                    <AppIcon name="edit" color={colors.accent} size={14} />
                  </AppPressable>
                  <AppPressable
                    style={styles.deleteItemBtn}
                    onPress={() => handleDeleteMemory(item.key)}
                  >
                    <AppIcon name="x" color={colors.muted} size={14} />
                  </AppPressable>
                </View>
              </View>

              {/* 记忆 Key 与 Value */}
              <View style={styles.contentWrap}>
                <Text style={styles.keyText} numberOfLines={1}>
                  {item.key}
                </Text>
                <Text style={styles.valueText}>
                  {item.value}
                </Text>
              </View>
            </View>
          )}
        />
      )}

      {/* 手动新增/编辑记忆弹窗 */}
      <Modal
        transparent
        animationType="slide"
        visible={editorVisible}
        onRequestClose={() => setEditorVisible(false)}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : "height"}
          style={styles.modalBackdrop}
        >
          <AppPressable style={StyleSheet.absoluteFill} onPress={() => setEditorVisible(false)} />
          <View style={styles.editorSheet}>
            <View style={styles.sheetHeader}>
              <View style={styles.sheetHeaderTitleWrap}>
                <AppIcon name="edit" color={colors.accent} size={16} />
                <Text style={styles.sheetTitle}>
                  {editingKey ? t("memories.editMemory") : t("memories.addMemory")}
                </Text>
              </View>
              <AppPressable style={styles.sheetCloseBtn} onPress={() => setEditorVisible(false)}>
                <AppIcon name="x" color={colors.muted} size={18} />
              </AppPressable>
            </View>

            <ScrollView
              style={styles.sheetScroll}
              contentContainerStyle={styles.sheetContent}
              keyboardShouldPersistTaps="handled"
            >
              {/* 键名 Key */}
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

              {/* 重要度 Importance */}
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

              {/* 分类 Category */}
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

              {/* 内容 Value */}
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

            {/* 底部按钮 */}
            <View style={styles.sheetFooter}>
              <AppPressable
                disabled={saving}
                style={styles.sheetCancelBtn}
                onPress={() => setEditorVisible(false)}
              >
                <Text style={styles.sheetCancelBtnText}>{t("memories.cancel")}</Text>
              </AppPressable>

              <AppPressable
                disabled={saving}
                style={[styles.sheetSaveBtn, saving && styles.sheetSaveBtnDisabled]}
                onPress={handleSaveMemory}
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
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  headerRightActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  addHeaderBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: radii.pill,
    backgroundColor: colors.accentSoft,
  },
  addHeaderBtnText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  heroBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.medium,
    padding: spacing.large,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  heroIconWrap: {
    width: 40,
    height: 40,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
    justifyContent: "center",
    alignItems: "center",
  },
  heroTextWrap: {
    flex: 1,
    gap: 3,
  },
  heroTitle: {
    color: colors.ink,
    fontSize: 16,
    fontWeight: "700",
  },
  heroSubtitle: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16,
  },
  heroAddBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.accent,
  },
  heroAddBtnText: {
    color: colors.onAccent,
    fontSize: 12,
    fontWeight: "700",
  },
  clearHeaderBtn: {
    padding: 6,
    borderRadius: radii.small,
  },
  filtersBar: {
    flexDirection: "row",
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.medium,
    gap: spacing.small,
  },
  filterChip: {
    paddingHorizontal: spacing.medium,
    paddingVertical: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  filterChipActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  filterChipText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
  },
  filterChipTextActive: {
    color: colors.onAccent,
    fontWeight: "700",
  },
  loadingWrap: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  list: {
    padding: spacing.large,
    gap: spacing.medium,
    paddingBottom: 48,
  },
  emptyContainer: {
    marginTop: 64,
    alignItems: "center",
    gap: spacing.medium,
    paddingHorizontal: spacing.large,
  },
  emptyIconWrap: {
    width: 64,
    height: 64,
    borderRadius: radii.large,
    backgroundColor: colors.surfaceMuted,
    justifyContent: "center",
    alignItems: "center",
  },
  emptyTitle: {
    color: colors.ink,
    fontSize: 17,
    fontWeight: "700",
  },
  emptyBody: {
    color: colors.muted,
    fontSize: 13,
    textAlign: "center",
    lineHeight: 19,
    maxWidth: 320,
  },
  emptyAddBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: spacing.small,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: radii.pill,
    backgroundColor: colors.accent,
  },
  emptyAddBtnText: {
    color: colors.onAccent,
    fontSize: 13,
    fontWeight: "700",
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.large,
    gap: spacing.medium,
    ...shadows.card,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  cardHeaderLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  cardHeaderRight: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  editItemBtn: {
    padding: 5,
    borderRadius: radii.small,
    backgroundColor: colors.accentSoft,
  },
  deleteItemBtn: {
    padding: 5,
    borderRadius: radii.small,
    backgroundColor: colors.surfaceMuted,
  },
  categoryBadge: {
    backgroundColor: colors.surfaceMuted,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radii.small,
  },
  categoryBadgeText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600",
  },
  importanceBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radii.small,
  },
  importanceCore: {
    backgroundColor: colors.accentSoft,
  },
  importanceCoreText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
  },
  importanceRelevant: {
    backgroundColor: colors.surfaceMuted,
  },
  importanceRelevantText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "600",
  },
  importanceText: {
    fontSize: 11,
  },
  confidencePill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  confidenceLabel: {
    color: colors.muted,
    fontSize: 11,
  },
  confidenceValue: {
    color: colors.ink,
    fontSize: 11,
    fontWeight: "700",
  },
  contentWrap: {
    gap: 4,
  },
  keyText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
    fontFamily: "monospace",
  },
  valueText: {
    color: colors.ink,
    fontSize: 14,
    lineHeight: 20,
    fontWeight: "500",
  },
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
    fontSize: 14,
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
    fontSize: 14,
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
    fontSize: 14,
    fontWeight: "700",
  },
});
