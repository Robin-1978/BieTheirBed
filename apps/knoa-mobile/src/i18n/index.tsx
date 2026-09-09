import * as SecureStore from "expo-secure-store";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";

import { en } from "./locales/en";
import { zh } from "./locales/zh";

export type LanguageMode = "system" | "zh-CN" | "en-US";
type MessageParams = Record<string, string | number>;

const STORAGE_KEY = "knoa.ui.language.v1";

type MessageKey = keyof typeof zh;
export type { MessageKey };
export { en, zh };

type I18nValue = {
  mode: LanguageMode;
  locale: "zh-CN" | "en-US";
  t(key: MessageKey, params?: MessageParams): string;
  setMode(mode: LanguageMode): Promise<void>;
};

const systemLocale = (): "zh-CN" | "en-US" =>
  Intl.DateTimeFormat().resolvedOptions().locale.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: PropsWithChildren) {
  const [mode, setStoredMode] = useState<LanguageMode>("system");
  const locale = mode === "system" ? systemLocale() : mode;

  const t = useCallback(
    (key: MessageKey, params: MessageParams = {}) => {
      const template: string = locale === "en-US" ? en[key] ?? zh[key] : zh[key];
      return Object.entries(params).reduce<string>(
        (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
        template,
      );
    },
    [locale],
  );

  useEffect(() => {
    let active = true;
    void SecureStore.getItemAsync(STORAGE_KEY).then((stored) => {
      if (active && (stored === "system" || stored === "zh-CN" || stored === "en-US")) {
        setStoredMode(stored);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const setMode = useCallback(async (next: LanguageMode) => {
    setStoredMode(next);
    await SecureStore.setItemAsync(STORAGE_KEY, next);
  }, []);

  const value = useMemo(() => ({ mode, locale, t, setMode }), [locale, mode, setMode, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) throw new Error("I18nProvider is missing");
  return value;
}
