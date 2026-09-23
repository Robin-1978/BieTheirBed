import { useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import MemoriesScreen from "../memories";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, typography } from "@/theme";

import UnifiedAssetsScreen from "./assets";

type Segment = "assets" | "memories";

/**
 * Space tab: one home for everything Knoa knows — artifacts/task results
 * and memories — with a single segment switch instead of two deep routes.
 * The legacy /(tabs)/assets and /memories routes keep working.
 */
export default function SpaceScreen() {
  const { t } = useI18n();
  const [segment, setSegment] = useState<Segment>("assets");

  return (
    <View style={styles.container}>
      <View style={styles.segment}>
        {(["assets", "memories"] as const).map((item) => {
          const active = segment === item;
          return (
            <AppPressable
              key={item}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              style={[styles.segmentItem, active && styles.segmentActive]}
              onPress={() => setSegment(item)}
            >
              <Text style={[styles.segmentText, active && styles.segmentTextActive]}>
                {item === "assets" ? t("tabs.assets") : t("memories.title")}
              </Text>
            </AppPressable>
          );
        })}
      </View>
      {segment === "assets" ? (
        <View style={styles.page}>
          <UnifiedAssetsScreen />
        </View>
      ) : (
        <View style={styles.page}>
          <MemoriesScreen embedded />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  page: { flex: 1 },
  segment: {
    flexDirection: "row",
    gap: spacing.small,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
  },
  segmentItem: {
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.xsmall,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.line,
  },
  segmentActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  segmentText: { ...typography.caption, color: colors.muted, fontWeight: "700" },
  segmentTextActive: { color: colors.onAccent },
});
