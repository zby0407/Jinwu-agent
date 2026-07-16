"use client";

import { createContext, useCallback, useContext, useEffect } from "react";
import { Toaster } from "sonner";
import { type ResolvedTheme, type Theme } from "@/lib/theme";

interface ThemeContextValue {
  /** The app is locked to dark mode. */
  theme: Theme;
  /** The actually-applied theme. */
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme: Theme = "dark";
  const resolvedTheme: ResolvedTheme = "dark";

  useEffect(() => {
    applyTheme("dark");
  }, []);

  const setTheme = useCallback((_next: Theme) => {}, []);

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return ctx;
}

/** Sonner toaster that follows the resolved theme (must render inside ThemeProvider). */
export function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return <Toaster theme={resolvedTheme} />;
}
