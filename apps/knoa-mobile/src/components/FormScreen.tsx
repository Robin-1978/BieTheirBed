import { router } from "expo-router";
import React, { useEffect } from "react";
import {
  Alert,
  BackHandler,
  type StyleProp,
  StyleSheet,
  type ViewStyle,
} from "react-native";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";

import { useI18n } from "@/i18n";
import { colors } from "@/theme";

export interface FormScreenProps {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  contentContainerStyle?: StyleProp<ViewStyle>;
  bottomOffset?: number;
  extraKeyboardSpace?: number;
  keyboardShouldPersistTaps?: "always" | "never" | "handled";
  showsVerticalScrollIndicator?: boolean;
  isDirty?: boolean;
  dirtyTitle?: string;
  dirtyMessage?: string;
  onDiscard?: () => void;
}

export function FormScreen({
  children,
  style,
  contentContainerStyle,
  bottomOffset = 24,
  extraKeyboardSpace = 16,
  keyboardShouldPersistTaps = "handled",
  showsVerticalScrollIndicator = false,
  isDirty = false,
  dirtyTitle,
  dirtyMessage,
  onDiscard,
}: FormScreenProps) {
  const { t } = useI18n();

  useEffect(() => {
    if (!isDirty) return;

    const onBackPress = () => {
      Alert.alert(
        dirtyTitle || t("common.unsavedChangesTitle"),
        dirtyMessage || t("common.unsavedChangesMessage"),
        [
          { text: t("common.cancel"), style: "cancel" },
          {
            text: t("common.discardChanges"),
            style: "destructive",
            onPress: () => {
              if (onDiscard) onDiscard();
              else router.back();
            },
          },
        ],
      );
      return true;
    };

    const subscription = BackHandler.addEventListener("hardwareBackPress", onBackPress);
    return () => subscription.remove();
  }, [dirtyMessage, dirtyTitle, isDirty, onDiscard, t]);

  return (
    <KeyboardAwareScrollView
      style={[styles.container, style]}
      contentContainerStyle={contentContainerStyle}
      bottomOffset={bottomOffset}
      extraKeyboardSpace={extraKeyboardSpace}
      keyboardShouldPersistTaps={keyboardShouldPersistTaps}
      showsVerticalScrollIndicator={showsVerticalScrollIndicator}
    >
      {children}
    </KeyboardAwareScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
});
