import React from "react";
import { ActivityIndicator, Alert, Linking, StyleSheet, Text, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { colors, radii, spacing, typography } from "@/theme";
import type { ActionCardButton, ActionCardStatus } from "./types";

export type CardActionFooterProps = {
  actions: ActionCardButton[];
  status: ActionCardStatus;
  resolvingActionId: string | null;
  onActionPress: (action: ActionCardButton) => void;
};

export function CardActionFooter({
  actions,
  status,
  resolvingActionId,
  onActionPress,
}: CardActionFooterProps) {
  if (!actions || actions.length === 0) return null;

  const isPending = status === "pending";
  const isBusy = Boolean(resolvingActionId);

  const handlePress = (action: ActionCardButton) => {
    if (!isPending || isBusy) return;

    if (action.action_type === "open_url" && action.url) {
      Linking.openURL(action.url).catch(() => undefined);
      return;
    }

    if (action.confirm_dialog) {
      Alert.alert(
        action.confirm_dialog.title,
        action.confirm_dialog.message,
        [
          { text: action.confirm_dialog.cancel_text || "取消", style: "cancel" },
          {
            text: action.confirm_dialog.confirm_text || "确定",
            style: action.style === "danger" ? "destructive" : "default",
            onPress: () => onActionPress(action),
          },
        ]
      );
      return;
    }

    onActionPress(action);
  };

  return (
    <View style={styles.container}>
      {actions.map((action) => {
        const isResolving = resolvingActionId === action.id;
        const buttonStyle = getButtonStyle(action.style);

        return (
          <AppPressable
            key={action.id}
            style={[
              styles.button,
              buttonStyle.container,
              (!isPending || isBusy) && styles.disabledButton,
            ]}
            disabled={!isPending || isBusy}
            onPress={() => handlePress(action)}
          >
            {isResolving ? (
              <ActivityIndicator
                size="small"
                color={action.style === "primary" || action.style === "danger" ? colors.onAccent : colors.ink}
              />
            ) : (
              <Text style={[styles.buttonText, buttonStyle.text]}>
                {action.label}
              </Text>
            )}
          </AppPressable>
        );
      })}
    </View>
  );
}

function getButtonStyle(style?: "primary" | "secondary" | "danger" | "outline") {
  switch (style) {
    case "primary":
      return {
        container: { backgroundColor: colors.accent, borderColor: colors.accent },
        text: { color: colors.onAccent },
      };
    case "danger":
      return {
        container: { backgroundColor: colors.danger, borderColor: colors.danger },
        text: { color: colors.onAccent },
      };
    case "secondary":
      return {
        container: { backgroundColor: colors.surfaceMuted, borderColor: colors.line },
        text: { color: colors.ink },
      };
    case "outline":
    default:
      return {
        container: { backgroundColor: "transparent", borderColor: colors.lineStrong },
        text: { color: colors.ink },
      };
  }
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "flex-end",
    alignItems: "center",
    gap: spacing.small,
    marginTop: spacing.small,
  },
  button: {
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
    borderRadius: radii.medium,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 38,
  },
  buttonText: {
    fontSize: typography.body.fontSize,
    fontWeight: "700",
  },
  disabledButton: {
    opacity: 0.5,
  },
});
