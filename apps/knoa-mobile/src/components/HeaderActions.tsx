import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors } from "@/theme";

type PrimaryScreen = "chat" | "tasks";

export function HeaderActions({ current }: { current: PrimaryScreen }) {
  return (
    <View style={styles.container}>
      <HeaderTab
        label="对话"
        selected={current === "chat"}
        onPress={() => router.replace("/chat")}
      />
      <HeaderTab
        label="任务"
        selected={current === "tasks"}
        onPress={() => router.replace("/tasks")}
      />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="设置与状态"
        hitSlop={8}
        onPress={() => router.push("/capabilities")}
        style={styles.action}
      >
        <Text style={styles.actionLabel}>设置</Text>
      </Pressable>
    </View>
  );
}

function HeaderTab({
  label,
  selected,
  onPress,
}: {
  label: string;
  selected: boolean;
  onPress(): void;
}) {
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      accessibilityLabel={label}
      disabled={selected}
      hitSlop={8}
      onPress={onPress}
      style={styles.action}
    >
      <Text style={[styles.label, selected && styles.selectedLabel]}>{label}</Text>
      <View style={[styles.indicator, selected && styles.selectedIndicator]} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    marginRight: 2,
  },
  action: {
    minHeight: 36,
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 2,
  },
  label: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "600",
  },
  selectedLabel: {
    color: colors.accent,
  },
  actionLabel: {
    color: colors.ink,
    fontSize: 15,
    fontWeight: "600",
  },
  indicator: {
    position: "absolute",
    bottom: 2,
    width: 16,
    height: 2,
    borderRadius: 1,
    backgroundColor: "transparent",
  },
  selectedIndicator: {
    backgroundColor: colors.accent,
  },
});
