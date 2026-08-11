import { router } from "expo-router";
import { StyleSheet, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { navigatePrimary, type PrimaryScreen } from "@/components/PrimarySwipeNavigation";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

export function HeaderActions({ current }: { current: PrimaryScreen }) {
  const { t } = useI18n();
  return (
    <View style={styles.container}>
      <HeaderTab
        icon="chat"
        label={t("header.chat")}
        selected={current === "chat"}
        onPress={() => navigatePrimary(current, "chat")}
      />
      <HeaderTab
        icon="tasks"
        label={t("header.tasks")}
        selected={current === "tasks"}
        onPress={() => navigatePrimary(current, "tasks")}
      />
      <AppPressable
        accessibilityRole="button"
        accessibilityLabel={t("common.settings")}
        hitSlop={8}
        onPress={() => router.push("/capabilities")}
        style={[styles.action, styles.settingsAction]}
      >
        <AppIcon name="settings" color={colors.accent} size={20} />
      </AppPressable>
    </View>
  );
}

function HeaderTab({
  icon,
  label,
  selected,
  onPress,
}: {
  icon: "chat" | "tasks";
  label: string;
  selected: boolean;
  onPress(): void;
}) {
  return (
    <AppPressable
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      accessibilityLabel={label}
      disabled={selected}
      hitSlop={8}
      onPress={onPress}
      style={styles.action}
    >
      <View style={[styles.tabIcon, selected && styles.selectedTabIcon]}>
        <AppIcon name={icon} color={selected ? colors.accent : colors.muted} size={21} />
      </View>
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginRight: 2,
  },
  action: {
    width: 40,
    height: 40,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: 13,
  },
  settingsAction: { backgroundColor: colors.accentSoft },
  tabIcon: { width: 36, height: 34, alignItems: "center", justifyContent: "center", borderRadius: 11 },
  selectedTabIcon: { backgroundColor: colors.accentSoft },
});
