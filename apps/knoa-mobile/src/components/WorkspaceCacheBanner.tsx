import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import {
  workspaceCacheFreshness,
  type WorkspaceCacheSnapshot,
} from "@/storage/workspaceCache";
import { colors, radii, spacing, typography } from "@/theme";

export function WorkspaceCacheBanner({
  snapshot,
  loading,
  error,
  onRefresh,
}: {
  snapshot: WorkspaceCacheSnapshot | null;
  loading: boolean;
  error: string;
  onRefresh(): void;
}) {
  const { t, locale } = useI18n();
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // Reset dismissed state when new data arrives or error state changes
    setDismissed(false);
  }, [error, snapshot?.updatedAt]);

  if (!snapshot || dismissed) return null;

  const freshness = workspaceCacheFreshness(snapshot);
  const timestamp = new Date(snapshot.updatedAt).toLocaleTimeString(locale === "en-US" ? "en-US" : "zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const message = error
    ? t("workspace.cacheOffline")
    : loading
      ? t("workspace.cacheRefreshing")
      : freshness === "stale"
        ? t("workspace.cacheStale", { time: timestamp })
        : t("workspace.cacheUpdated", { time: timestamp });

  return (
    <View style={[styles.banner, error ? styles.error : freshness === "stale" ? styles.warning : styles.normal]}>
      <View style={styles.contentWrap}>
        {error ? (
          <AppIcon name="alert" color={colors.danger} size={16} />
        ) : null}
        <Text style={styles.message} numberOfLines={2}>{message}</Text>
      </View>
      <View style={styles.actions}>
        <AppPressable accessibilityRole="button" onPress={onRefresh} disabled={loading} style={styles.actionButton}>
          <Text style={styles.action}>{loading ? t("workspace.cacheRefreshingAction") : t("common.refresh")}</Text>
        </AppPressable>
        <AppPressable
          accessibilityRole="button"
          accessibilityLabel={t("common.close")}
          onPress={() => setDismissed(true)}
          style={styles.closeButton}
          hitSlop={8}
        >
          <AppIcon name="x" color={colors.muted} size={15} />
        </AppPressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.small,
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    borderWidth: 1,
  },
  contentWrap: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  actions: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  actionButton: {
    paddingVertical: 2,
    paddingHorizontal: 4,
  },
  closeButton: {
    padding: 4,
  },
  normal: { backgroundColor: colors.surface, borderColor: colors.line },
  warning: { backgroundColor: colors.warningSoft, borderColor: colors.warning },
  error: { backgroundColor: colors.dangerSoft, borderColor: colors.danger },
  message: { flex: 1, color: colors.ink, ...typography.small, lineHeight: 17 },
  action: { color: colors.accent, ...typography.small, fontWeight: "800" },
});
