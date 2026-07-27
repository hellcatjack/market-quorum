/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { EN_US, renderMessage } from "./messages";
import type { MessageKey, MessageVariables } from "./messages";

export type UiLocale = "zh-CN" | "en-US";

export const UI_LOCALE_STORAGE_KEY = "tradingng.ui.locale";

export function resolveLocale(
  storedLocale: string | null,
  browserLanguages: readonly string[],
): UiLocale {
  if (storedLocale === "zh-CN" || storedLocale === "en-US") return storedLocale;
  for (const language of browserLanguages) {
    const normalized = language.toLowerCase();
    if (normalized.startsWith("zh")) return "zh-CN";
    if (normalized.startsWith("en")) return "en-US";
  }
  return "en-US";
}

export function translate(
  locale: UiLocale,
  key: MessageKey,
  variables?: MessageVariables,
): string {
  const message = locale === "zh-CN" ? key : EN_US[key];
  return renderMessage(message, variables);
}

function detectedLocale(): UiLocale {
  if (typeof window === "undefined" || typeof navigator === "undefined") return "en-US";
  let storedLocale: string | null = null;
  try {
    storedLocale = window.localStorage.getItem(UI_LOCALE_STORAGE_KEY);
  } catch {
    // Storage can be unavailable in locked-down browsers; browser language still works.
  }
  return resolveLocale(storedLocale, navigator.languages ?? [navigator.language]);
}

interface I18nContextValue {
  locale: UiLocale;
  setLocale: (locale: UiLocale) => void;
  t: (key: MessageKey, variables?: MessageVariables) => string;
  formatDateTime: (value: string | Date) => string;
  formatPercent: (value: number) => string;
  formatDuration: (milliseconds: number | null) => string;
}

const defaultContext: I18nContextValue = {
  locale: "zh-CN",
  setLocale: () => undefined,
  t: (key, variables) => translate("zh-CN", key, variables),
  formatDateTime: (value) => new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value)),
  formatPercent: (value) => new Intl.NumberFormat("zh-CN", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(value),
  formatDuration: (milliseconds) => milliseconds === null ? "—" : `${milliseconds} 毫秒`,
};

const I18nContext = createContext<I18nContextValue>(defaultContext);

export function I18nProvider({
  children,
  initialLocale,
}: {
  children: ReactNode;
  initialLocale?: UiLocale;
}) {
  const [locale, setLocaleState] = useState<UiLocale>(() => initialLocale ?? detectedLocale());

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((nextLocale: UiLocale) => {
    setLocaleState(nextLocale);
    document.documentElement.lang = nextLocale;
    try {
      window.localStorage.setItem(UI_LOCALE_STORAGE_KEY, nextLocale);
    } catch {
      // The UI remains switched even when storage is unavailable.
    }
  }, []);

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    setLocale,
    t: (key, variables) => translate(locale, key, variables),
    formatDateTime: (date) => new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(date)),
    formatPercent: (number) => new Intl.NumberFormat(locale, {
      style: "percent",
      maximumFractionDigits: 2,
    }).format(number),
    formatDuration: (milliseconds) => {
      if (milliseconds === null) return "—";
      if (milliseconds < 1_000) return locale === "zh-CN"
        ? `${milliseconds} 毫秒`
        : `${milliseconds} ms`;
      return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(milliseconds / 1_000)
        + (locale === "zh-CN" ? " 秒" : " s");
    },
  }), [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}
