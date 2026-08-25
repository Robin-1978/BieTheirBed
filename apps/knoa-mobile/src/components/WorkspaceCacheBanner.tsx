import { StyleSheet, Text, View } from "react-native";

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
  if (!snapshot) return null;
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
      <Text style={styles.message}>{message}</Text>
      <AppPressable accessibilityRole="button" onPress={onRefresh} disabled={loading}>
        <Text style={styles.action}>{loading ? t("workspace.cacheRefreshingAction") : t("common.refresh")}</Text>
      </AppPressable>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.small, padding: spacing.medium, borderRadius: radii.medium, borderWidth: 1 },
  normal: { backgroundColor: colors.surface, borderColor: colors.line },
  warning: { backgroundColor: colors.warningSoft, borderColor: colors.warning },
  error: { backgroundColor: colors.dangerSoft, borderColor: colors.danger },
  message: { flex: 1, color: colors.muted, ...typography.small, lineHeight: 17 },
  action: { color: colors.accent, ...typography.small, fontWeight: "800" },
});
