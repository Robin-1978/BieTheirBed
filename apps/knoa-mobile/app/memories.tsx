import { Stack } from "expo-router";
import * as Clipboard from "expo-clipboard";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  RefreshControl,
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
  searchMemories,
  type MemoryFilter,
} from "@/components/memoryPresentation";
import { useI18n } from "@/i18n";
import { MemoryEditorSheet, type MemoryDraft } from "@/components/MemoryEditorSheet";
import { useConnection, useSession } from "@/state/GatewayProvider";
import { colors, radii, shadows, spacing, typography } from "@/theme";

const EMPTY_DRAFT: MemoryDraft = { key: "", value: "", category: "preference", importance: "core" };

export default function MemoriesScreen() {
  const { t } = useI18n();
  const gateway = useSession();
  const { status } = useConnection();

  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [clearing, setClearing] = useState(false);
  const [filter, setFilter] = useState<MemoryFilter>("all");

  // 编辑/新增弹窗状态（表单细节见 MemoryEditorSheet）
  const [editorVisible, setEditorVisible] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<MemoryDraft>(EMPTY_DRAFT);

  const load = useCallback(async () => {
    if (!gateway.client || status !== "ready") {
      setLoading(false);
      return;
    }
    setRefreshing(true);
    setLoadError("");
    try {
      const response = await gateway.runAuthenticated((client) => client.listMemories());
      setMemories(response.items || []);
    } catch {
      setLoadError(t("memories.loadFailed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [gateway.client, gateway.runAuthenticated, status, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleOpenCreate = useCallback(() => {
    setEditingKey(null);
    setDraft(EMPTY_DRAFT);
    setEditorVisible(true);
  }, []);

  const handleOpenEdit = useCallback((item: MemoryRecord) => {
    setEditingKey(item.key);
    setDraft({ key: item.key, value: item.value, category: item.category, importance: item.importance });
    setEditorVisible(true);
  }, []);

  const handleClearAll = useCallback(() => {
    Alert.alert(
      t("memories.clearAll"),
      t("memories.clearConfirm"),
      [
        { text: t("common.cancel"), style: "cancel" },
        {
          text: t("memories.exportBackup"),
          onPress: () => void exportBackup(),
        },
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

  const exportBackup = useCallback(async () => {
    try {
      await Clipboard.setStringAsync(JSON.stringify(memories, null, 2));
      Alert.alert("", t("memories.exported"));
    } catch {
      Alert.alert("", t("memories.exportFailed"));
    }
  }, [memories, t]);

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
    () => searchMemories(filterMemories(memories, filter), searchQuery),
    [filter, memories, searchQuery],
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

      {/* 搜索框 */}
      <View style={styles.searchBar}>
        <AppIcon name="globe" color={colors.muted} size={16} />
        <TextInput
          accessibilityLabel={t("memories.searchPlaceholder")}
          value={searchQuery}
          onChangeText={setSearchQuery}
          placeholder={t("memories.searchPlaceholder")}
          placeholderTextColor={colors.muted}
          style={styles.searchInput}
          returnKeyType="search"
        />
        {searchQuery ? (
          <AppPressable
            accessibilityLabel={t("common.cancel")}
            onPress={() => setSearchQuery("")}
            style={styles.searchClear}
          >
            <AppIcon name="x" color={colors.muted} size={14} />
          </AppPressable>
        ) : null}
      </View>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.accent} size="large" />
        </View>
      ) : loadError && memories.length === 0 ? (
        <View style={styles.emptyContainer}>
          <View style={styles.emptyIconWrap}>
            <AppIcon name="history" color={colors.muted} size={36} />
          </View>
          <Text style={styles.emptyTitle}>{t("memories.loadFailed")}</Text>
          <AppPressable style={styles.emptyAddBtn} onPress={() => void load()}>
            <Text style={styles.emptyAddBtnText}>{t("memories.retry")}</Text>
          </AppPressable>
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
                <Text style={styles.sourceText} numberOfLines={1}>
                  {item.source && !item.source.startsWith("explicit")
                    ? t("memories.sourceAgent")
                    : t("memories.sourceManual")} · {t("memories.scopeNote")}
                </Text>
              </View>
            </View>
          )}
        />
      )}

      {/* 手动新增/编辑记忆弹窗 */}
      <MemoryEditorSheet
        visible={editorVisible}
        editingKey={editingKey}
        initial={draft}
        hasClient={Boolean(gateway.client)}
        runAuthenticated={gateway.runAuthenticated}
        onClose={() => setEditorVisible(false)}
        onSaved={() => {
          setEditorVisible(false);
          void load();
        }}
      />
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
  searchBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    marginHorizontal: spacing.large,
    paddingHorizontal: spacing.medium,
    minHeight: 44,
    borderRadius: radii.medium,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
  },
  searchInput: {
    flex: 1,
    color: colors.ink,
    fontSize: 15,
    paddingVertical: spacing.small,
  },
  searchClear: {
    width: 32,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
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
    fontSize: 15,
    lineHeight: 20,
    fontWeight: "500",
  },
  sourceText: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 15,
  },
});
