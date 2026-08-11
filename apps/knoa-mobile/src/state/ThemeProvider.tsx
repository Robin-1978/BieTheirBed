import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import { Appearance, NativeModules, Platform, useColorScheme } from "react-native";

export type ThemeMode = "system" | "light" | "dark";

type NativeThemeModule = {
  getMode(): Promise<ThemeMode>;
  setMode(mode: ThemeMode): Promise<void>;
};

const nativeTheme = NativeModules.KnoaTheme as NativeThemeModule | undefined;

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

export function ThemeProvider({ children }: PropsWithChildren) {
  const [mode, setStoredMode] = useState<ThemeMode>("system");
  const scheme = useColorScheme();

  useEffect(() => {
    if (Platform.OS !== "android" || !nativeTheme) return;
    let active = true;
    void nativeTheme.getMode().then((stored) => {
      if (active && (stored === "system" || stored === "light" || stored === "dark")) setStoredMode(stored);
    });
    return () => { active = false; };
  }, []);

  const setMode = useCallback(async (next: ThemeMode) => {
    setStoredMode(next);
    // The native preference is applied before React Native starts on the next
    // launch. Appearance is only changed for an explicit user action so the
    // current view updates immediately without re-applying the mode during
    // startup and causing an Activity restart loop.
    Appearance.setColorScheme(next === "system" ? "unspecified" : next);
    if (Platform.OS === "android" && nativeTheme) await nativeTheme.setMode(next);
  }, []);

  const value = useMemo<ThemePreference>(() => ({
    mode,
    resolved: mode === "system" ? (scheme === "dark" ? "dark" : "light") : mode,
    setMode,
  }), [mode, scheme, setMode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useThemePreference() {
  return useContext(ThemeContext);
}
