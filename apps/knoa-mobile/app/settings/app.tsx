import * as Application from "expo-application";
import { router } from "expo-router";
import { type ReactNode } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n, type LanguageMode } from "@/i18n";
import { useThemePreference, type ThemeMode } from "@/state/ThemeProvider";
import { colors } from "@/theme";

export default function AppSettingsScreen() {
  const theme = useThemePreference();
  const i18n = useI18n();

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Section title={i18n.t("settings.appearance")} detail={i18n.t("settings.appearanceHint")}>
        <View accessibilityRole="radiogroup" style={styles.choices}>
          <Choice label={i18n.t("settings.theme.system")} mode="system" selected={theme.mode === "system"} onPress={theme.setMode} />
          <Choice label={i18n.t("settings.theme.light")} mode="light" selected={theme.mode === "light"} onPress={theme.setMode} />
          <Choice label={i18n.t("settings.theme.dark")} mode="dark" selected={theme.mode === "dark"} onPress={theme.setMode} />
        </View>
      </Section>

      <Section title={i18n.t("settings.language")} detail={i18n.t("settings.languageHint")}>
        <View accessibilityRole="radiogroup" style={styles.choices}>
          <Choice label={i18n.t("settings.language.system")} mode="system" selected={i18n.mode === "system"} onPress={i18n.setMode} />
          <Choice label={i18n.t("settings.language.zh")} mode="zh-CN" selected={i18n.mode === "zh-CN"} onPress={i18n.setMode} />
          <Choice label={i18n.t("settings.language.en")} mode="en-US" selected={i18n.mode === "en-US"} onPress={i18n.setMode} />
        </View>
      </Section>

      <View style={styles.card}>
        <View style={styles.versionRow}>
          <View style={styles.versionIcon}><AppIcon name="settings" color={colors.accent} size={23} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{i18n.t("settings.appVersion")}</Text>
            <Text style={styles.detail}>
              {Application.nativeApplicationVersion ?? i18n.t("settings.development")} ({Application.nativeBuildVersion ?? "—"})
            </Text>
          </View>
        </View>
        <AppPressable onPress={() => router.push("/update")} style={styles.updateButton}>
          <AppIcon name="refresh" color="white" size={20} />
          <Text style={styles.updateText}>{i18n.t("settings.checkAppUpdate")}</Text>
        </AppPressable>
        <Text style={styles.updateHint}>{i18n.t("settings.checkAppUpdateHint")}</Text>
      </View>
    </ScrollView>
  );
}

function Section({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <View style={styles.card}>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.detail}>{detail}</Text>
      {children}
    </View>
  );
}

function Choice<T extends ThemeMode | LanguageMode>({
  label,
  mode,
  selected,
  onPress,
}: {
  label: string;
  mode: T;
  selected: boolean;
  onPress(mode: T): Promise<void>;
}) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => void onPress(mode)}
      style={({ pressed }) => [styles.choice, selected && styles.choiceSelected, pressed && styles.pressed]}
    >
      <View style={[styles.radio, selected && styles.radioSelected]}>{selected ? <View style={styles.radioDot} /> : null}</View>
      <Text style={[styles.choiceLabel, selected && styles.choiceLabelSelected]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 14, paddingBottom: 48 },
  card: { padding: 16, gap: 10, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 17, fontWeight: "800" },
  detail: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  choices: { gap: 7, marginTop: 2 },
  choice: { minHeight: 48, flexDirection: "row", alignItems: "center", gap: 11, paddingHorizontal: 13, borderRadius: 13, borderWidth: 1, borderColor: colors.line },
  choiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  choiceLabel: { color: colors.ink, fontWeight: "700" },
  choiceLabelSelected: { color: colors.accent, fontWeight: "800" },
  radio: { width: 20, height: 20, alignItems: "center", justifyContent: "center", borderRadius: 10, borderWidth: 2, borderColor: colors.muted },
  radioSelected: { borderColor: colors.accent },
  radioDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent },
  pressed: { opacity: 0.72 },
  versionRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  versionIcon: { width: 46, height: 46, alignItems: "center", justifyContent: "center", borderRadius: 14, backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  updateButton: { minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderRadius: 13, backgroundColor: colors.accent },
  updateText: { color: "white", fontWeight: "800" },
  updateHint: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
