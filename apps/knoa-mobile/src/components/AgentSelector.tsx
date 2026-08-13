import { Pressable, StyleSheet, Text, View } from "react-native";

import type { AgentSummary } from "@/api/models";
import { colors } from "@/theme";

export function AgentSelector({
  agents,
  selectedAgentId,
  disabled = false,
  label,
  lockedLabel,
  onChange,
}: {
  agents: AgentSummary[];
  selectedAgentId: string;
  disabled?: boolean;
  label: string;
  lockedLabel: string;
  onChange(agentId: string): void;
}) {
  if (!agents.length) return null;
  return (
    <View style={styles.container}>
      <Text style={styles.label}>{disabled ? lockedLabel : label}</Text>
      <View style={styles.options}>
        {agents.map((agent) => {
          const selected = agent.agent_id === selectedAgentId;
          return (
            <Pressable
              accessibilityRole="radio"
              accessibilityState={{ checked: selected, disabled }}
              disabled={disabled}
              key={agent.agent_id}
              onPress={() => onChange(agent.agent_id)}
              style={({ pressed }) => [
                styles.option,
                selected && styles.selected,
                pressed && !disabled && styles.pressed,
                disabled && !selected && styles.disabled,
              ]}
            >
              <Text style={[styles.optionText, selected && styles.selectedText]}>{agent.display_name}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 7 },
  label: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  options: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  option: { minHeight: 34, justifyContent: "center", paddingHorizontal: 13, borderRadius: 17, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  selected: { borderColor: colors.accent, backgroundColor: colors.accentFaint },
  optionText: { color: colors.muted, fontSize: 13, fontWeight: "700" },
  selectedText: { color: colors.accent },
  pressed: { opacity: 0.65 },
  disabled: { opacity: 0.45 },
});
