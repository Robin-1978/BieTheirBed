import { Modal, Pressable, StyleSheet, Text, View } from "react-native";

import type { AgentSummary, UnavailableAgent } from "@/api/models";
import { AgentSelector } from "@/components/AgentSelector";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { agentReasonLabel } from "@/components/chat";
import { useI18n } from "@/i18n";
import { colors, radii, spacing } from "@/theme";

export function AgentPickerSheet({
  visible,
  agents,
  unavailableAgents,
  selectedAgentId,
  agentLocked,
  onClose,
  onSelect,
  onConfigure,
}: {
  visible: boolean;
  agents: AgentSummary[];
  unavailableAgents: UnavailableAgent[];
  selectedAgentId: string;
  agentLocked: boolean;
  onClose(): void;
  onSelect(agentId: string): void;
  onConfigure(): void;
}) {
  const { t } = useI18n();
  return (
    <Modal
      animationType="fade"
      onRequestClose={onClose}
      transparent
      visible={visible}
    >
      <View style={styles.modalRoot}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.actionSheet}>
          <View style={styles.sheetHandle} />
          <AgentSelector
            agents={agents}
            selectedAgentId={selectedAgentId}
            label={agentLocked ? t("agent.changeConversation") : t("agent.selectConversation")}
            lockedLabel={t("agent.lockedConversation")}
            onChange={onSelect}
          />
          {unavailableAgents.length ? (
            <View style={styles.unavailableAgents}>
              <Text style={styles.unavailableTitle}>{t("agent.unavailableTitle")}</Text>
              {unavailableAgents.map((agent) => (
                <Text key={agent.agent_id} style={styles.unavailableText}>
                  {agent.display_name} · {agentReasonLabel(agent.reason, t)}
                </Text>
              ))}
              <AppPressable
                style={styles.configureUnavailable}
                onPress={onConfigure}
              >
                <Text style={styles.configureUnavailableText}>{t("agent.configureUnavailable")}</Text>
                <AppIcon name="chevron-right" color={colors.accent} size={16} />
              </AppPressable>
            </View>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFill, backgroundColor: "rgba(0, 0, 0, 0.4)" },
  actionSheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radii.large,
    borderTopRightRadius: radii.large,
    padding: spacing.large,
    gap: spacing.medium,
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.line,
    alignSelf: "center",
  },
  unavailableAgents: {
    marginTop: spacing.small,
    paddingTop: spacing.medium,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    gap: spacing.xsmall,
  },
  unavailableTitle: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  unavailableText: { color: colors.muted, fontSize: 12 },
  configureUnavailable: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: spacing.small,
  },
  configureUnavailableText: { color: colors.accent, fontSize: 12, fontWeight: "700" },
});
