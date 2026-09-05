import { Stack } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
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

export default function MemoriesScreen() {
  const { t } = useI18n();
  const gateway = useGateway();

  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [filter, setFilter] = useState<MemoryFilter>("all");

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
            memories.length > 0 ? (
              <AppPressable
                disabled={clearing}
                onPress={handleClearAll}
                style={styles.clearHeaderBtn}
              >
                {clearing ? (
                  <ActivityIndicator color={colors.danger} size="small" />
                ) : (
                  <Text style={styles.clearHeaderBtnText}>{t("memories.clearAll")}</Text>
                )}
              </AppPressable>
            ) : null
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

                <View style={styles.confidencePill}>
                  <Text style={styles.confidenceLabel}>{t("memories.confidence")}</Text>
                  <Text style={styles.confidenceValue}>
                    {formatConfidencePercent(item.confidence)}
                  </Text>
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
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
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
  clearHeaderBtn: {
    paddingHorizontal: spacing.medium,
    paddingVertical: 6,
  },
  clearHeaderBtnText: {
    color: colors.danger,
    fontSize: 13,
    fontWeight: "600",
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
});
