import React, { useState } from "react";
import { StyleSheet, View } from "react-native";

import { colors, radii, shadows, spacing } from "@/theme";
import { CardActionFooter } from "./CardActionFooter";
import { CardBlockRenderer } from "./CardBlockRenderer";
import { CardFormRenderer } from "./CardFormRenderer";
import { CardHeader } from "./CardHeader";
import type { ActionCard, ActionCardButton, ActionCardInvocation } from "./types";

export type ActionCardViewProps = {
  card: ActionCard;
  onInvokeAction?: (invocation: ActionCardInvocation) => Promise<void> | void;
};

export function ActionCardView({ card, onInvokeAction }: ActionCardViewProps) {
  const [formValues, setFormValues] = useState<Record<string, unknown>>(() => {
    const initial: Record<string, unknown> = {};
    if (card.inputs) {
      for (const input of card.inputs) {
        if (input.default_value !== undefined) {
          initial[input.id] = input.default_value;
        }
      }
    }
    return initial;
  });

  const [resolvingActionId, setResolvingActionId] = useState<string | null>(null);

  const handleFormChange = (id: string, value: unknown) => {
    setFormValues((prev) => ({ ...prev, [id]: value }));
  };

  const handleActionPress = async (action: ActionCardButton) => {
    if (!onInvokeAction) return;

    setResolvingActionId(action.id);
    try {
      const finalArguments = action.include_form_inputs
        ? { ...action.arguments, ...formValues }
        : action.arguments;

      await onInvokeAction({
        card_id: card.card_id,
        action_id: action.id,
        action_type: action.action_type || "invoke_tool",
        tool_name: action.tool_name,
        arguments: finalArguments,
      });
    } finally {
      setResolvingActionId(null);
    }
  };

  const isExpiredOrDone = card.status !== "pending";

  return (
    <View style={[styles.card, isExpiredOrDone && styles.cardInactive]}>
      <CardHeader
        title={card.title}
        subtitle={card.subtitle}
        level={card.level}
        status={card.status}
        source={card.source}
      />

      {card.blocks && card.blocks.length > 0 ? (
        <CardBlockRenderer blocks={card.blocks} />
      ) : null}

      {card.inputs && card.inputs.length > 0 ? (
        <CardFormRenderer
          inputs={card.inputs}
          values={formValues}
          onChange={handleFormChange}
          disabled={isExpiredOrDone || Boolean(resolvingActionId)}
        />
      ) : null}

      {card.actions && card.actions.length > 0 ? (
        <CardActionFooter
          actions={card.actions}
          status={card.status}
          resolvingActionId={resolvingActionId}
          onActionPress={handleActionPress}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.large,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.medium,
    gap: spacing.small,
    marginVertical: spacing.small,
    ...shadows.card,
  },
  cardInactive: {
    opacity: 0.85,
    backgroundColor: colors.surfaceMuted,
  },
});
