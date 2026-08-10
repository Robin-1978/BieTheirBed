import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors } from "@/theme";

export function PrimaryNav({ current }: { current: "chat" | "tasks" }) {
  return (
    <View accessibilityRole="tablist" style={styles.nav}>
      <NavItem label="对话" selected={current === "chat"} onPress={() => router.replace("/chat")} />
      <NavItem label="任务" selected={current === "tasks"} onPress={() => router.replace("/tasks")} />
    </View>
  );
}

function NavItem({ label, selected, onPress }: { label: string; selected: boolean; onPress(): void }) {
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      accessibilityLabel={label}
      onPress={onPress}
      style={[styles.item, selected && styles.selected]}
    >
      <Text style={[styles.label, selected && styles.selectedLabel]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  nav: { flexDirection: "row", borderTopWidth: 1, borderTopColor: colors.line, padding: 8, gap: 8, backgroundColor: colors.surface },
  item: { flex: 1, minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: 12 },
  selected: { backgroundColor: colors.accentSoft },
  label: { color: colors.muted, fontWeight: "700" },
  selectedLabel: { color: colors.accent },
});
