import * as SecureStore from "expo-secure-store";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import { Appearance, useColorScheme, type ColorSchemeName } from "react-native";

export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "knoa.ui.theme.v1";

type ThemePreference = {
  mode: ThemeMode;
  resolved: "light" | "dark";
  setMode(mode: ThemeMode): Promise<void>;
};

const ThemeContext = createContext<ThemePreference>({
  mode: "system",
  resolved: "light",
  setMode: async () => undefined,
});

function applyMode(mode: ThemeMode) {
  Appearance.setColorScheme((mode === "system" ? null : mode) as ColorSchemeName);
}

export function ThemeProvider({ children }: PropsWithChildren) {
  const [mode, setStoredMode] = useState<ThemeMode>("system");
  const scheme = useColorScheme();

  useEffect(() => {
    let active = true;
    void SecureStore.getItemAsync(STORAGE_KEY).then((stored) => {
      if (!active || (stored !== "system" && stored !== "light" && stored !== "dark")) return;
      setStoredMode(stored);
      applyMode(stored);
    });
    return () => { active = false; };
  }, []);

  const setMode = useCallback(async (next: ThemeMode) => {
    applyMode(next);
    setStoredMode(next);
    await SecureStore.setItemAsync(STORAGE_KEY, next);
  }, []);

  const value = useMemo<ThemePreference>(() => ({
    mode,
    resolved: scheme === "dark" ? "dark" : "light",
    setMode,
  }), [mode, scheme, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useThemePreference() {
  return useContext(ThemeContext);
}
